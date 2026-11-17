import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import amber_sync as sync
import site_builder as site
from storage import CHUNK, MARGIN, PublicationSpace
import test_sync as fixtures


class SuccessfulRetentionTests(unittest.TestCase):
    setUp = fixtures.SyncTests.setUp
    tearDown = fixtures.SyncTests.tearDown

    def test_five_successes_beyond_five_tags_survive_repeated_failure_and_restart(self):
        bad = []
        for i in range(1,11):
            release = self.api.add(version=f'1.0.{i}')
            if i >= 9:
                bad.append((release,release['assets'][0]['digest']))
                release['assets'][0]['digest'] = None
        for cycle in range(7):
            # Even after process restarts, failed releases consume neither slot.
            self.engine = sync.Engine(self.config,self.api)
            with self.assertRaisesRegex(sync.SyncError,'PRODUCT_SYNC_FAILED'):
                self.engine.sync_product('AMBER')
            ready = {r['version'] for r in self.engine.state['records'].values() if r['status']=='ready'}
            self.assertEqual(len(ready),min(cycle+1,5))
        self.assertEqual(ready,{'1.0.8','1.0.7','1.0.6','1.0.5','1.0.4'})
        self.assertEqual(self.engine.plan()['candidates'],[])
        self.engine.manifest()
        index=json.loads((self.root/'public/mirror/index.json').read_text())
        self.assertEqual(set(index['products']['AMBER']['versions']),ready)
        # Completing one formerly invalid release yields six successes; only
        # the oldest success is proposed for reversible quarantine.
        bad[-1][0]['assets'][0]['digest'] = bad[-1][1]
        with self.assertRaisesRegex(sync.SyncError,'PRODUCT_SYNC_FAILED'):
            self.engine.sync_product('AMBER')
        self.assertEqual([c['id'] for c in self.engine.plan()['candidates']],['AMBER/1.0.4'])

    def test_budget_rejects_zero_or_bool_instead_of_silent_starvation(self):
        self.api.add()
        for value in (0,-1,True,6):
            self.engine.config['backfill_per_run']=value
            with self.assertRaisesRegex(sync.SyncError,'INVALID_BACKFILL_BUDGET'):
                self.engine.sync_product('AMBER')


@unittest.skipUnless(os.name=='posix','Real POSIX allocation, atomic rename and subprocess crash tests')
class RecoveryAndSpaceTests(unittest.TestCase):
    setUp = fixtures.DeploymentTests.setUp
    tearDown = fixtures.DeploymentTests.tearDown
    create_site = fixtures.DeploymentTests.create_site

    def pair(self):
        self.create_site('a'*40,'1.0.1')
        self.create_site('b'*40,'1.0.2')

    def candidate(self,commit='c'*40):
        source=self.root/('candidate-'+commit)
        (source/'src').mkdir(parents=True)
        (source/'src/download-catalog.json').write_text(json.dumps({
            p:{'repo':repo,'version':'1.0.3'} for p,repo in sync.PRODUCTS.items()}))
        static=source/'dist/client'
        (static/'assets').mkdir(parents=True)
        (static/'index.html').write_text('<script src="/assets/new.js"></script>')
        (static/'assets/new.js').write_text('console.log("new")')
        return source,static

    def test_actual_process_exits_at_each_rollback_commit_boundary_then_timer_stays_paused(self):
        self.pair()
        configfile=self.root/'config.json'
        configfile.write_text(json.dumps(self.config))
        script='''
import os,sys,json
sys.path.insert(0,sys.argv[1])
import amber_sync,site_builder
def checkpoint(name):
    if name==sys.argv[3]: os._exit(73)
site_builder.transaction_checkpoint=checkpoint
engine=amber_sync.Engine(json.load(open(sys.argv[2])))
site_builder.rollback(engine)
'''
        original=copy.deepcopy(self.engine.state)
        for point in ('rollback_intent_saved','rollback_pointer_switched','rollback_complete_saved'):
            with self.subTest(point=point):
                site.switch(self.engine,'b'*40,'a'*40)
                self.engine.state=copy.deepcopy(original)
                self.engine.save()
                result=subprocess.run([sys.executable,'-c',script,str(Path(sync.__file__).parent),str(configfile),point],
                                      capture_output=True,timeout=15)
                self.assertEqual(result.returncode,73,result.stderr.decode())
                restarted=sync.Engine(self.config,self.api)
                self.assertEqual(site.active_pair(restarted),('a'*40,'b'*40))
                self.assertTrue(restarted.state['website']['paused'])
                self.assertEqual(restarted.state['website']['rollback']['phase'],'complete')
                with patch.object(restarted.api,'json',create=True,side_effect=AssertionError('must not query/deploy main')):
                    with self.assertRaisesRegex(sync.SyncError,'WEBSITE_PAUSED_AFTER_ROLLBACK'):
                        site.sync_website(restarted)
                site.rollback(restarted) # retry is idempotent, not a toggle back to main
                self.assertEqual(site.active_pair(restarted),('a'*40,'b'*40))

    def test_failed_intent_write_never_switches_and_resume_is_explicit(self):
        self.pair()
        with patch.object(self.engine,'save',side_effect=OSError('injected state failure')):
            with self.assertRaises(OSError): site.rollback(self.engine)
        restarted=sync.Engine(self.config,self.api)
        self.assertEqual(site.active_pair(restarted),('b'*40,'a'*40))
        self.assertFalse(restarted.state['website']['paused'])
        site.rollback(restarted)
        site.resume_website(restarted)
        after=sync.Engine(self.config,self.api)
        self.assertFalse(after.state['website']['paused'])
        self.assertNotIn('rollback',after.state['website'])

    def test_recovery_refuses_an_unrelated_pointer_set(self):
        self.pair()
        self.engine.state['website']={'commit':'b'*40,'paused':True,'rollback':{
            'id':'a'*32,'phase':'pending','from':['b'*40,'a'*40],'to':['a'*40,'b'*40]}}
        self.engine.save()
        site.switch(self.engine,'a'*40,None)
        with self.assertRaisesRegex(sync.SyncError,'ROLLBACK_POINTER_CONFLICT'):
            sync.Engine(self.config,self.api)
        self.assertTrue(json.loads(self.engine.state_path.read_text())['website']['paused'])

    def test_history_is_budgeted_and_exact_budget_boundary_is_enforced(self):
        self.pair()
        old=self.root/'public/sites'/('b'*40)/'assets/large-old.bin'
        old.write_bytes(b'x'*(2*CHUNK))
        source,static=self.candidate()
        metadata=sync.encoded({'schema':1,'commit':'c'*40,'pins':site.source_pins(source)})+b'\n'
        files,dirs,guard,budget=site.publication_plan(self.engine,static,'b'*40,metadata)
        self.assertIn('assets/large-old.bin',files)
        new_only=sum(guard.allocated(p.stat().st_size) for p in static.rglob('*') if p.is_file())
        self.assertGreaterEqual(budget,new_only+2*CHUNK)
        reserve=1024*1024
        self.engine.config['reserve_bytes']=reserve
        free=reserve+MARGIN+budget-1
        with patch('amber_sync.shutil.disk_usage',return_value=type('Usage',(),{'free':free})()):
            with self.assertRaisesRegex(sync.SyncError,'INSUFFICIENT_DISK_SPACE'):
                site.publish_site(self.engine,source,static,'c'*40)
        self.assertEqual(site.active_pair(self.engine),('b'*40,'a'*40))
        self.assertFalse((self.root/'public/sites'/('c'*40)).exists())
        # Exactly sufficient preflight bytes are accepted by the same guard.
        with patch('amber_sync.shutil.disk_usage',return_value=type('Usage',(),{'free':free+1})()):
            guard.check(budget)

    def test_duplicate_history_is_not_counted_twice(self):
        self.pair()
        source,static=self.candidate()
        old=self.root/'public/sites'/('b'*40)/'assets/identical.bin'
        old.write_bytes(b'z'*16384)
        (static/'assets/identical.bin').write_bytes(old.read_bytes())
        files,_,_,_=site.publication_plan(self.engine,static,'b'*40,b'{}')
        self.assertEqual(sum(n=='assets/identical.bin' for n in files),1)

    def test_reserve_drop_during_copy_releases_new_allocation_and_preserves_both_sites(self):
        self.pair()
        source,static=self.candidate()
        (static/'assets/new.js').write_bytes(b'x'*(3*CHUNK))
        original_check=PublicationSpace.check
        original_copy=PublicationSpace.copy
        checks=[]
        active={'new':False}
        def copying(guard,source,target,size):
            active['new']=target.name=='new.js'
            try: return original_copy(guard,source,target,size)
            finally: active['new']=False
        def failure(guard,required=0):
            if required==0 and active['new']:
                checks.append(True)
                if len(checks)==3:
                    raise sync.SyncError('INSUFFICIENT_DISK_SPACE')
            return original_check(guard,required)
        with patch.object(PublicationSpace,'check',new=failure), patch.object(PublicationSpace,'copy',new=copying):
            with self.assertRaisesRegex(sync.SyncError,'INSUFFICIENT_DISK_SPACE'):
                site.publish_site(self.engine,source,static,'c'*40)
        self.assertEqual(site.active_pair(self.engine),('b'*40,'a'*40))
        self.assertEqual((static/'assets/new.js').stat().st_size,3*CHUNK)
        staged=list((self.root/'private/staging').rglob('new.js'))
        self.assertEqual(len(staged),1)
        self.assertEqual(staged[0].stat().st_size,0)


if __name__=='__main__': unittest.main()
