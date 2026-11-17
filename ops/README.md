# 官网与 Release 镜像运维

本目录是可独立审查的部署源。**提交 PR 不会安装服务、合并 main、发布 Release 或改动现有服务器。** 先完成独立审查、用户验收及服务器预检，再按下列步骤启用。不要把密码、SSH 私钥、GitHub 令牌、证书私钥、运行目录或安装包加入仓库或聊天。

## 已知基线与尚待现场确认的项目

- 官网仓库 `ayr1k/ayr1k.github.io`，本任务以远端 main `8adb58a7a3dd1f169ed4977a62404e30a84ed01e` 为基线。
- 两个产品的 1.7.4 正式 Release 均提供 GitHub API `digest: sha256:…`，各有五个支持的安装包，另有 SHA256SUMS、Windows provenance 和源代码 ZIP。
- 用户报告：Ubuntu 24.04、Nginx、HTTPS、Certbot 续期模拟已通过；`~/amber-web-work/site/src/DownloadPage.jsx` 有未提交的 Windows 备用下载按钮；旧文件在 `/var/www/html/downloads/1.7.4/`。
- 任务环境没有可用的 SSH 配置/已验证主机记录，直接网络访问受限；本地浏览器接口也未能安全连接。**未读取服务器补丁、Nginx 配置、磁盘、证书或下载文件；没有进行服务器写入。** 上述服务器信息是用户提供的基线，不是本次现场验证结果。
- 服务器补丁仍须保存并逐行对照本 PR。当前实现按用户描述将备用按钮持久化，并扩展到已有各平台；不能声称已导入未读取的原补丁。

## 分离与提交边界

| 内容 | 路径 / 行为 |
| --- | --- |
| 同步程序 / 配置 / 工具 | `/usr/local/lib/amber-sync`、`/etc/amber-sync`、`/opt/amber-web-tools`，root 拥有，运行用户只读 |
| 独立运行用户 | `amber-sync` 同步/发布；`amber-build` 安装依赖与断网构建；`amber-fetch` 仅运行受限 registry 代理；均无登录 shell、sudo、SSH 或证书私钥权限 |
| 构建请求 | `/srv/amber-web-requests`，sync 拥有，build 组只读；2750 继承组，文件 0640 |
| 构建结果/工作区 | `/srv/amber-web-build`，build 拥有，sync 组只读；2750 / 0640 |
| 构建根目录 | `/srv/amber-web-build-jail`，root 拥有；只挂载系统运行库、工具、只读请求和可写构建目录 |
| 私有状态、缓存、锁、暂存、清理计划、隔离区 | `/srv/amber-sync/private`，0700 |
| 新镜像 | `/srv/amber-sync/public/mirror/{amber,ambere}/{version}` |
| 静态网站与回滚版本 | `/srv/amber-sync/public/sites/{commit}`，仅复制 `dist/client` |
| 原子部署指针 | `public/active -> site-sets/{id}`，每组同时包含 current 与 previous |
| 旧工作副本 / 旧下载目录 | 保留原位；程序完全不读写旧工作副本，不接管或清理旧下载文件 |

官网跟踪 main 提交；AMBER 和 AmberE 各自跟踪正式 Release，互不等待。页面只使用 `src/download-catalog.json` 为该产品指定的版本；新 Release 本身不会把旧页面版本悄悄升级。编辑发布内容时分别更新 catalog 的版本及相应文案，原有首页/功能页面的固定发布文案仍由官网维护流程更新。

镜像入口使用完整的产品、版本、文件名和大小匹配。页面读取公开 manifest，再 HEAD 探测文件；匹配成功才显示“服务器备用下载 / Server mirror”，GitHub 按钮一直保留。点击镜像前再次探测；失败则转向该产品、该版本的 GitHub 文件。完整传输途中断网仍需要用户使用旁边的 GitHub 按钮；浏览器不能替用户无缝接续两个来源。没有国内速度承诺，不修改软件内自动更新渠道。

## 实际支持与排除范围

`P` 为精确产品名 `AMBER` 或 `AmberE`，`V` 为不带 v 的 `major.minor.patch`。当前两款 1.7.4 的五个安装包均在范围内。FreeBSD 包仍是服务器/工具实验包，不代表 Minecraft 客户端支持。

| 平台 | 必须匹配的文件名（前缀均为 `P-V-`） |
| --- | --- |
| Windows | `Setup-{x64,arm64,ia32}.{exe,msi}`；`windows-{x64,arm64,ia32}.zip` |
| macOS | `mac-{x64,arm64,universal}.{zip,dmg,pkg}` |
| Linux | `linux-{x64,arm64}.{deb,rpm,AppImage,tar.gz}` |
| FreeBSD | `freebsd-{x64,arm64}-experimental.tar.gz` |
| 校验元数据 | `SHA256SUMS` / `SHA256SUMS.txt`、上述安装包的 `.provenance.json`；每份元数据本身也必须有可信 GitHub SHA-256 |

自动生成 `_release.json`（Release/asset 身份、大小、可信摘要、排除项）与 `SHA256SUMS.mirror.txt`。GitHub 的独立上传源代码 `P-V-source.zip`、自动生成源码包、`.blockmap`、自动更新 JSON、签名及其他非安装包不镜像；GitHub 原链接保留。未知可执行文件或归档不是静默忽略：报告 `UNRECOGNIZED_INSTALLER_REQUIRES_REVIEW`，待新增明确命名规则及测试。正式版标签只接受 `vX.Y.Z`；草稿/预发布先过滤，其他正式标签要求人工审查。

可信摘要只取固定官方仓库的 HTTPS GitHub Release API `digest`。**不能用自己刚下载文件算出的摘要代替可信来源。** 旧 Release 缺 API 摘要时暂停该版本，不能仅依赖缺乏自身可信摘要的 SHA256SUMS。GitHub SHA-256 是来自仓库/平台的完整性信任，不提供额外作者签名或免除 GitHub 账号被攻陷的风险。原始安装包不解包、不执行。

## 同步、失败与保留策略

- 默认每半小时检查一次，加最多 60 秒抖动；systemd 同一 service 与进程间文件锁双重排斥并发，手动操作共用同一把锁。
- API 分页最多 20 页，每页 100；超过上限报错。ETag 304 是经服务器确认的无变化；503、403/429、断网、异常 JSON 都是失败，不能使用旧响应伪装成功。公共仓库不需要令牌，不读取 SSH、netrc 或代理凭据。403/429 立即失败等下次定时器；其他短暂错误最多三次尝试、1/2 秒退避。单个 HTTP 连接 30 秒，单文件 10 分钟，整次任务 28 分钟。
- 数值排序版本（1.10 高于 1.9），不按发布日期或 GitHub 的 latest 指针降级。按版本数值从新到旧扫描，直到得到五个通过完整校验的成功版本；失败不算保留名额，候选可超过上游最近五个标签；默认每产品每轮最多成功同步一个新版本，避免首次回填耗尽带宽与 API 配额。完整回填通常需要五轮，最新版本优先。单个异常版本独立报告，不阻止其他可校验候选回填；已知未解决的异常会持久化并继续检查，即使本轮成功下载名额已用尽也不能悄悄忽略；即使回填成功，本轮仍报告存在的失败，页面也不会跨版本替换链接。已经管理的历史版本继续检查是否被撤回或资产改变。
- 资产第一次可信观察即写身份日志；同名替换、asset ID/大小/摘要/更新时间/资产列表变化、撤回为草稿或预发布均报告失败，不覆盖既有文件。已管理版本异常时从 manifest 隐藏（held），原始字节保留。若上游恢复原身份且本地校验通过，可恢复 ready；人为接受替换需要独立审查及备份，不提供“忽略哈希”开关。
- 下载在私有暂存目录完成，逐文件检查名称、产品、版本、大小与摘要；SHA256SUMS 若存在还必须与 API 摘要一致。传输后重新取完整 Release/asset 列表，再对比身份。整个版本目录经校验、fsync 后原子重命名公开；中断可从 pending 日志识别已完成的公开目录，不接管陌生同名目录。
- 任一组件失败不阻止其他组件继续；结构化 JSON 日志和私有 status.json 保留失败代码及各组件上次成功时间。定时任务整体返回非零，不能把部分成功记作整体成功。
- 每产品最近五个**成功同步**版本在活动镜像中保留。当前/上一版页面的引用额外保护，允许超过五个。调度任务只生成清理计划，不自动执行。审阅后显式 `quarantine PLAN_ID` 会重新验证状态、清单、页面引用和有效期，再原子移入私有可恢复隔离区。计划超过 24 小时、状态变化、未知文件或符号链接均拒绝。
- 隔离会释放公开路径，**不释放磁盘空间**。`restore` 使用原文件、清单和摘要恢复，支持中断后恢复。程序没有永久删除命令；也不清理 GitHub、旧 `/downloads/`、非管理文件或网站回滚目录。若需要真正回收磁盘，先备份到独立存储并验证可恢复性，再就具体永久删除列表另行取得用户确认。不要运行通配递归删除命令。
- 失败暂存、构建请求/结果/工作目录与历史网站也保留以供诊断。它们可能增长；监控容量、定期审阅，确认可从固定 Git 提交/锁文件重建后，将具体列表走单独清理流程。任务宁可报告空间不足，也不冒险删数据。

## 部署前：先只读核对并保存原始文件

以下在用户自行打开并验证的服务器会话中执行。不要将 SSH 或证书私钥传到聊天。先在终端本地检查 `whoami`、`hostname`、`lsb_release -ds`、`df -h / /var/www/html`、`findmnt -T /var/www/html`，确认目标主机正确。保存日志的目录必须是私有目录。

```sh
umask 077
backup="$HOME/amber-migration-$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -m 700 "$backup"
cd "$HOME/amber-web-work/site"
git status --short > "$backup/git-status.txt"
git rev-parse HEAD > "$backup/original-commit.txt"
git diff --binary > "$backup/working-tree.patch"
git diff --binary -- src/DownloadPage.jsx > "$backup/server-download-buttons.patch"
cp -p src/DownloadPage.jsx "$backup/DownloadPage.server.jsx"
git ls-files --others --exclude-standard > "$backup/untracked-files.txt"
tar --exclude=node_modules --exclude=.git -czf "$backup/server-site.tar.gz" .
sudo nginx -T > "$backup/nginx-config.txt" 2>&1
sudo tar -C /var/www -czf "$backup/original-html.tar.gz" html
sudo chown "$(id -u):$(id -g)" "$backup/original-html.tar.gz"
sha256sum "$backup/server-download-buttons.patch" "$backup/original-html.tar.gz"
```

本地检查完整补丁及未跟踪文件列表，逐项核对本 PR 的 DownloadPage/mirror 实现，不能假定只有两个按钮。若还有本 PR 未整合的改动，停止上线，把非敏感补丁加入同一任务分支审查。**保留上述目录、旧站点与旧下载目录；本流程不对旧工作副本 git pull/reset/clean。** Nginx 完整配置只保存在服务器私有目录，勿公开上传。备份如因权限或空间失败，停在此处。

核对线上四种产品/语言组合、Windows/macOS/Linux/FreeBSD、主题商店、所有旧 `/downloads/1.7.4/` 地址；在服务器和外部设备分别以 HTTPS 下载校验当前两份 Windows 安装包。证书检查用 `sudo certbot certificates`，定时器用 `systemctl list-timers --all`，只看路径/到期时间，不读取私钥文件内容。腾讯云控制台另行确认实例到期、自动续费/余额、流量配额和快照；不要在聊天提供账单或账号凭据。

## 安装（独立审查及用户验收后，由管理员操作）

先确认 Python 3.12+、git、ca-certificates 和 systemd 已安装；缺少时通过 Ubuntu 官方软件源安装。不需要 bubblewrap 或允许非特权用户命名空间，也不修改 AppArmor、防火墙或 SSH。真实 Ubuntu 24.04 CI 已发现非特权 UID 映射被拒绝，因此实现使用 root 管理的固定 systemd 单元创建挂载根目录，再以独立非 root 账户运行构建。

`amber-sync` 只向固定请求文件写入随机 job ID、固定提交、源码 tar 摘要和页面版本引用。`amber-web-build.path` 监听该文件，启动 root 拥有的固定 `amber-web-build.service`，以 `amber-build` 身份进入 RootDirectory。源码 tar 在运行前验证摘要并安全提取；构建账户看不到同步私有状态、镜像、主目录、SSH 或证书。仅 `/build` 可写，`/requests` 和 `/usr`、`/tools` 只读；服务终止时 systemd 杀死所有子进程。同步账户等待服务完全结束，逐文件核验结果及副本摘要，再发布自己的副本。服务账户没有启动任意 systemd 单元或执行 sudo 的权限。

依赖安装使用 `--frozen-lockfile --ignore-scripts --ignore-pnpmfile`；所有 pnpm 调用（包括版本查询）都禁用项目 pnpmfile。依赖安装与构建/测试由两个独立 systemd 单元串行执行，各自有独立网络命名空间。固定 Node 24.22.1 / pnpm 11.25.0；更改版本必须同步工具安装、源码 pin、单元挂载和测试。main 不会在同步服务宿主上执行 pnpm。
```sh
# 在本 PR 的全新、已审核检出目录中；不要使用旧生产工作副本。
python3 ops/bootstrap_tools.py "$HOME/amber-tools-candidate"
# 检查 tool-receipt.json，Node 官方 SHA256SUMS / npm pnpm SHA-512 已由脚本校验。
# 目录必须是全新目标；如目标已存在，先调查，不覆盖未知文件。
sudo install -d -o root -g root -m 755 /opt/amber-web-tools
sudo cp -a "$HOME/amber-tools-candidate/." /opt/amber-web-tools/
sudo chown -R root:root /opt/amber-web-tools
sudo chmod -R a+rX,go-w /opt/amber-web-tools
sudo useradd --system --user-group --no-create-home --shell /usr/sbin/nologin amber-sync
sudo useradd --system --user-group --no-create-home --shell /usr/sbin/nologin amber-build
sudo useradd --system --user-group --no-create-home --shell /usr/sbin/nologin amber-fetch
sudo install -d -o root -g root -m 755 /usr/local/lib/amber-sync /etc/amber-sync
sudo install -o root -g root -m 644 ops/amber_sync.py ops/site_builder.py ops/build_worker.py ops/storage.py ops/network_guard.py ops/registry_proxy.py /usr/local/lib/amber-sync/
sudo install -o root -g root -m 644 ops/config.example.json /etc/amber-sync/config.json
sudo install -d -o amber-sync -g amber-sync -m 711 /srv/amber-sync
sudo install -d -o amber-sync -g amber-sync -m 700 /srv/amber-sync/private
sudo install -d -o amber-sync -g amber-sync -m 755 /srv/amber-sync/public
sudo install -d -o amber-sync -g amber-build -m 2750 /srv/amber-web-requests
sudo install -d -o amber-build -g amber-sync -m 2750 /srv/amber-web-build /srv/amber-web-build/results
sudo install -d -o root -g root -m 755 /srv/amber-web-build-jail
printf '%s\n' 'AMBER build jail v1' | sudo tee /srv/amber-web-build-jail/.amber-build-jail >/dev/null
sudo chmod 444 /srv/amber-web-build-jail/.amber-build-jail
sudo ln -s usr/bin /srv/amber-web-build-jail/bin
sudo ln -s usr/sbin /srv/amber-web-build-jail/sbin
sudo ln -s usr/lib /srv/amber-web-build-jail/lib
sudo ln -s usr/lib64 /srv/amber-web-build-jail/lib64
sudo install -o root -g root -m 644 ops/systemd/amber-web-build.service ops/systemd/amber-web-build.path ops/systemd/amber-web-install.service ops/systemd/amber-fetch-proxy.service ops/systemd/amber-fetch-proxy.socket /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl start amber-fetch-proxy.socket
sudo systemctl start amber-web-build.path
```

如用户或目录已存在，先核对归属及内容，不能直接覆盖。所有 public/private/staging/quarantine/site-sets 必须在同一文件系统；Nginx 的 www-data 只需 public 的读取和目录穿越权限。服务账户不能修改 root 拥有的程序、配置或工具。需要安装更新版程序时先停止 timer/service 和 amber-web-build.path/amber-web-build.service/amber-web-install.service/amber-fetch-proxy.socket/amber-fetch-proxy.service，备份旧程序和状态，再只替换审核过的程序文件。

容量预算：按 1.7.4 规模，两产品五版本的受支持包约 **6.4 GiB**，另需保留旧站点/旧包、隔离版本、构建与诊断目录、至少 2 GiB 构建预算和 2 GiB 空间余量；构建服务自身还检查其所在文件系统至少有 4 GiB 可用。建议首次回填前有至少 15 GiB 可用空间，实际按磁盘和保留量核算；程序每次下载/发布仍检查空间。修改 reserve 不得低于实际系统/日志需要。

在隔离测试根目录验证实际 systemd 构建账户、固定工具安装、全量冻结依赖安装和以下所有测试，再运行一次候选构建。`config.example.json` 可复制到私有测试配置，`root` 指向 `/srv/amber-sync-staging`，`tools` 使用同一只读工具目录；不要把候选目录加入 Nginx。测试配置的 activation receipt 仍须如实记录已备份并核对的服务器改动。首次任务只能在本 PR 已批准合并后的 main 上运行，否则官网旧 main 缺少 catalog/tool pins 会明确拒绝构建。

```sh
python3 -m unittest discover -s ops/tests -v
/opt/amber-web-tools/node/bin/node --version
/opt/amber-web-tools/node/bin/node /opt/amber-web-tools/pnpm/bin/pnpm.cjs --version
# 本机/CI 的常规构建验证，生产服务还须验证独立构建服务内同样的命令：
pnpm install --frozen-lockfile --ignore-scripts
pnpm build
pnpm test:sites
pnpm test:mirror
PYTHONPATH=ops python3 -c 'from pathlib import Path; from site_builder import validate_static; print(validate_static(Path("dist/client")))'
# 必须实际验证生产使用的沙箱：全新 scratch 目录，不指向原站点或 runtime。
# 请求/结果目录按上述权限预置；将审核过的 tracked 源码放在全新 /srv/amber-review，
# root 拥有、amber-sync 组只读；不授权服务账户读取管理员的 home。
sudo install -d -o root -g amber-sync -m 750 /srv/amber-review
git archive HEAD | sudo tar -x -C /srv/amber-review
sudo chown -R root:amber-sync /srv/amber-review
sudo chmod -R u+rwX,g+rX,o-rwx /srv/amber-review
sudo -u amber-sync python3 /srv/amber-review/ops/tests/validate_sandbox.py --scratch /srv/amber-sync/private/worker-validation
```

完成原补丁整合、备份及 Nginx 配置审阅后，管理员在服务器**本地**创建 `/etc/amber-sync/activation.json`，root:root 0644，填入两个备份文件的实际 SHA-256。不要照抄占位值：

```json
{
  "base_commit": "8adb58a7a3dd1f169ed4977a62404e30a84ed01e",
  "server_patch_sha256": "实际 server-download-buttons.patch 的 64 位 SHA-256",
  "legacy_backup_sha256": "实际 original-html.tar.gz 的 64 位 SHA-256",
  "patch_reconciled": true,
  "nginx_checked": true
}
```

该文件是管理员完成检查的操作记录，不替代备份或独立审查。检查有缺项就不能创建记录。定时器暂不启用，先执行一次同步，让新目录生成且经过文件检查；当前 Nginx 仍使用旧目录：

```sh
sudo -u amber-sync /usr/bin/python3 /usr/local/lib/amber-sync/amber_sync.py --config /etc/amber-sync/config.json run
sudo -u amber-sync /usr/bin/python3 /usr/local/lib/amber-sync/amber_sync.py --config /etc/amber-sync/config.json status
```

必须确认三个组件均成功、new root 的 current 指向正确 main、镜像哈希和 `index.json` 匹配，下一步才切换生产。第一次部署还没有 previous，**旧 `/var/www/html` 就是首次回滚点**；后续才使用程序的 previous。

## 切换生产前的具体变更与回滚

将 `nginx/amber-sync.locations.conf` 按真实配置整合到**已有 HTTPS server**，备份原配置，不重复 root/location。保留 listen、server_name、现有 TLS、HTTP→HTTPS 与 Certbot/ACME 规则；核查是否有正则 location、alias、缓存或 disable_symlinks 影响新路径。若 ACME webroot 在 `/var/www/html`，保留其明确 location，不让新 root 改变续期路径。

具体切换只有：Nginx 静态 root 改到 `/srv/amber-sync/public/active/current`；新增 `/mirror/v1/` 与 manifest 路由；保留 `/downloads/` 指向原目录；安装 30 分钟定时器。不会修改证书、防火墙、SSH、软件更新配置或 GitHub Releases。清理默认只写计划，不移动、不删除。

```sh
sudo nginx -t
# 仅当语法检查成功、当前文件及首次回滚点已验证后 reload。
sudo systemctl reload nginx
# 从外部设备核对 HTTPS、四种产品/语言、各平台、主题及旧下载链接，并下载核验哈希。
sudo certbot renew --dry-run
sudo install -o root -g root -m 644 ops/systemd/amber-sync.service ops/systemd/amber-sync.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl start amber-sync.service
sudo systemctl status amber-sync.service --no-pager
sudo systemctl enable --now amber-sync.timer
systemctl list-timers amber-sync.timer
```

若首次切换失败，恢复备份的 Nginx 配置到原文件名、`nginx -t` 后 reload，root 回到 `/var/www/html`；新目录留存以便检查。不要把整个 `/etc/nginx` 无差别覆盖。

后续网站回滚：

```sh
sudo systemctl stop amber-sync.timer
sudo systemctl stop amber-sync.service
sudo -u amber-sync /usr/bin/python3 /usr/local/lib/amber-sync/amber_sync.py --config /etc/amber-sync/config.json rollback
# 实测 HTTPS 页面与镜像后再开 timer。网站跟踪会暂停，两个产品镜像仍可继续。
sudo systemctl start amber-sync.timer
# 独立审查并修复阻碍后才恢复 main 跟踪：
sudo -u amber-sync /usr/bin/python3 /usr/local/lib/amber-sync/amber_sync.py --config /etc/amber-sync/config.json resume-site
```

`rollback` 先原子持久化 `paused=true` 与 pending 回滚意图（记录 from/to 指针对），再切换 current/previous 整组指针，最后标记 complete。任一边界崩溃后，下次启动在访问 GitHub main 前恢复该事务：旧指针则完成切换，新指针则只完成状态提交，始终保持暂停。无法匹配 from/to 时拒绝继续，不覆盖第三方状态。重复 rollback 是幂等重试；恢复 main 跟踪必须显式 resume-site，它在一次原子状态写中清除回滚事务并取消暂停。保存所有历史静态目录，失败不会覆盖旧版本。旧 hashed assets 随下一版复制，避免切换期间已打开的标签页缺资源；同名不同内容报错。若只发生镜像任务失败，不需要网站回滚。

## 清理与恢复

```sh
sudo -u amber-sync /usr/bin/python3 /usr/local/lib/amber-sync/amber_sync.py --config /etc/amber-sync/config.json plan
# 在服务器本地查看 private/plans/<PLAN_ID>.json，核对明确候选列表及 protected。
# 管理员确认这是希望移入隔离区的具体列表后，替换下面的 PLAN_ID：
sudo -u amber-sync /usr/bin/python3 /usr/local/lib/amber-sync/amber_sync.py --config /etc/amber-sync/config.json quarantine PLAN_ID
sudo -u amber-sync /usr/bin/python3 /usr/local/lib/amber-sync/amber_sync.py --config /etc/amber-sync/config.json restore AMBER 1.0.1
```

最后一条只是示例，必须换成真实被隔离的产品和版本。每个指令都使用同一锁；任务并发时返回 ALREADY_RUNNING，不抢占现有任务。隔离被中断时，使用同一 `restore PRODUCT VERSION` 根据日志恢复，不能手动拼路径覆盖文件。不要篡改 state.json 或删除 observations 来接受替换资产。

## 维护与故障定位

| 情况 | 检查与恢复 |
| --- | --- |
| 任务失败 / 超时 / 限流 | `systemctl status amber-sync.service amber-web-build.service amber-web-install.service amber-fetch-proxy.service amber-fetch-proxy.socket amber-web-build.path`、`journalctl -u amber-sync.service -u amber-web-install.service -u amber-web-build.service -u amber-fetch-proxy.service --since '2 hours ago'`、私有 status.json 的各组件 ok/code/last_success；先排除网络和配额，再手动 start；失败保留旧站点、下载和指针 |
| 磁盘不足 | 停 timer，检查 `df -h`、私有暂存/隔离/历史网站与日志；先扩容或备份，按具体可恢复清单处理；不降低校验、不自动删未知数据 |
| 缺摘要 / 替换资产 | 固定该项，GitHub 下载仍可用；复核可信 Release，联系发布维护流程补齐/重新发布新版本，不能接受替换旧版本 |
| 状态文件损坏 | 停 timer/service；备份整个私有状态及公开元数据；从已验证状态备份恢复，逐文件核对；程序不重建或接管陌生目录 |
| Nginx/HTTPS 不通 | 检查 nginx -t、服务、域名 DNS 和公网可达性；恢复已备份配置；不要关闭 TLS 验证或防火墙 |
| 证书续期 | 确认 certbot timer 仍启用、ACME 路径未变、reload 后 dry-run 成功；到期前至少 30 天处理失败；不读取或上传私钥 |
| 服务器/流量到期 | 在腾讯云控制台维护到期提醒、余额/自动续费、流量和快照；建议至少提前 14 天核对，到期前延续服务；本程序不能续费服务器 |
| 告警 | 默认同步/依赖安装/断网构建/registry 代理的 journal 和退出码，不擅自给他人发消息。接入现有监控：service failed 立即报警，任一 last_success 超过 90 分钟、磁盘低于余量、HTTPS 外部探测失败及证书剩余 <30 天报警。未接监控前管理员需日常查看，不能假定已有推送通知 |

备份至少包括 root 拥有的配置/程序、private/state.json（含 observations）、当前/上一版网站及相应镜像元数据。隔离区和未知文件在永久清理前另行离机备份并恢复验证。备份运行期间先停 service/timer 或使用一致性快照，完成后恢复 timer。

## 验证边界

Python 测试覆盖：新版本、两产品不同步、数值排序、无更新、草稿/预发布、API/限流/304/分页、缺摘要、大小/哈希/元数据冲突、同名替换、传输中变化、本地损坏、空间不足、进程锁、发布中断恢复、清理计划/第六版/页面保护/陌生文件/隔离恢复、源码路径与静态引用。Linux 额外覆盖符号链接边界、原子切换、失败保留双指针、实际页面引用及回滚暂停。

CI 构建真实官网，运行原有 Sites 测试与新增镜像逻辑测试，校验静态引用；固定 Playwright 版本在隔离 Chromium 中验证中英文、两产品、五个安装包入口、桌面/手机、镜像失败与部分可用。静态预览和 ops 源码作为 PR Actions artifact 下载，名称包含被测试 head revision，不提供或执行软件安装包。

CI 不是生产服务器验收。上线前仍必须现场验证 Ubuntu 的 systemd 受限根目录与独立 UID、真实工具下载/安装、冻结依赖、Nginx/HTTPS/ACME、外部下载哈希、磁盘与权限、原补丁核对和生产回滚。未经这些检查不能宣称生产自动更新已启用。


构建服务需单独维护 `amber-web-build.path`；启动 timer 前确认 path active/waiting。RootDirectory 挂载由 systemd 系统管理器建立，进程本身没有 root/capabilities；不需要为运行用户开放 sudo 或系统单元管理权限。故障时看两个 service 和 path 的 journal，构建错误原始日志位于 build 工作目录下的 command-failure.log，只在服务器私有目录诊断，不公开上传。GitHub CI 的 provision_ci_worker.sh 只允许在一次性 GitHub runner 上执行，不能当成生产安装脚本。

真实沙箱集成测试会验证独立 UID、只读系统/请求目录、同步状态/镜像/证书不可见、清空凭据环境、依赖安装白名单代理以及断网构建，再实际安装固定依赖并构建。单元创建/启动、命名账户和目录安装均属于管理员的一次性部署步骤；不能仅在宿主运行 pnpm 来代替这项验证。


## 本轮修复：事务、网络与容量边界

回滚的意图记录与暂停位先于公开指针切换持久化，且私有状态与目录都 fsync。测试在意图保存后、指针切换后、完成记录保存后三个位置实际 `os._exit`，重启再执行 timer 的网站路径，确认不会查询/重新部署 main；另测保存失败、无关指针冲突和显式恢复。这里验证的是进程崩溃恢复，不等同于物理断电或磁盘控制器缓存故障测试。

保留选择以当前重新校验成功的 ready 版本排序，选择最近五个成功版本；缺摘要、哈希失败和被 held 的版本不占成功名额。已有异常记录会跨重启保留，有限下载名额用完不代表这些异常已解决。当前/回滚页面引用继续额外保护；隔离状态不会被自动回填撤销。两个最新标签都不可验证时，程序可继续补齐它们之后的五个可验证版本，不为旧页面替换为其他版本下载。

| 阶段 | systemd 单元 | 网络与权限 |
| --- | --- | --- |
| 依赖安装 | amber-web-install.service | RootDirectory + PrivateNetwork，仅有自身 lo；只能通过挂载的 Unix socket 访问受限代理；禁止生命周期脚本和 pnpmfile；无 host 网络接口 |
| registry 出口 | amber-fetch-proxy.socket / .service | 独立 amber-fetch 身份，只接受精确 `CONNECT registry.npmjs.org:443`；通过宿主 DNS 解析此固定域名；所有回答先拒绝私有、回环、链路本地、CGNAT、元数据、宿主自有地址及 IPv6 转换地址，再连接已校验的数字 IP，避免二次解析；不接收任意目的地/端口/重定向 |
| 构建及测试 | amber-web-build.service | 等安装单元及其子进程完全退出后启动；另一个 PrivateNetwork 命名空间；仅允许 AF_UNIX，不能创建 IPv4/IPv6/Netlink socket；不挂载代理 socket，无代理环境变量 |
| 接收及发布 | amber-sync.service | 验证 protocol v2 的安装/构建网络回执、提交、页面引用及逐文件哈希，再复制至自己的私有目录并发布 |

网络策略在运行时自检，失败不退回宿主网络。CI 会先在宿主开一个真实可达的回环监听器作为正对照，再验证两个阶段都无法访问该监听器、RFC1918、IPv4/IPv6 元数据、CGNAT 及公共 IP；代理的八个拒绝目标也逐一通过真实 Unix socket 验证。真实 npm 包下载是白名单出口的正对照。构建中的 Node 子进程另行尝试访问目标并验证失败。DNS 的混合公有/私有回答、IPv4 映射、NAT64/Teredo/6to4 和宿主公有 IP 另有注入式策略测试；未在真实云网络中劫持 registry DNS 或访问生产元数据。

只支持公共 npm registry；自建 registry、Git 依赖、额外 CDN、NAT64 出口或自定义 pnpmfile 所需能力应单独审查扩展，不自动放宽出口。依赖安装最长 11 分钟，断网构建最长 10 分钟，同步进程等待构建最长 22 分钟，整个同步 service 仍最长 28 分钟。监控必须包含 install/build/proxy 三个单元和 path/socket。重启后确保 proxy socket 与 build path 都已启用；首次验收通过后再运行 `sudo systemctl enable --now amber-fetch-proxy.socket amber-web-build.path`，最后启用原同步 timer。

网站接收副本和发布副本均使用受保护复制。发布预检按新产物与需继承的历史 assets 的**完整并集**计费：同路径同内容只计一次，冲突拒绝；按文件系统块对齐，加上元数据、目录和额外 1 MiB 安全余量。每个新暂存文件在写入前 posix_fallocate 全额预分配，并在分配后、每个复制块前后及 fsync 后核对剩余空间；低于 reserve 则中止，只截断本次创建的暂存副本以归还其分配，保留原始来源、当前站点和 previous。目标文件系统不支持可靠预分配时拒绝发布，不静默降级为无保护复制。

边界测试涵盖：新文件能放下但历史文件放不下、预检恰差一字节/刚好足够、重复历史文件不重复计费，以及复制中空间突然减少。对任意不受本程序控制的并发写入或任意构建脚本的磁盘消耗，用户态检查不能代替文件系统配额或独立数据卷；如需全系统硬保证，应在服务器验收时设置单独容量/配额并测试。程序不会修改宿主防火墙、SSH、AppArmor 或 userns 安全开关。
