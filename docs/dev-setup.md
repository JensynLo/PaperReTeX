# 个人开发环境

只存在于 `dev` 分支。`main` 只放项目本身；本文件、`.vscode/` 和依赖本地
`samples/` 的评测脚本都只在这里。

## 分支

- `main`：对外的项目代码与 README。功能改动先提交到 `main`。
- `dev`：在 `main` 之上多一个个人工具提交。`main` 前进后在 `dev` 上执行
  `git merge main` 跟上；不要把 `dev` 合回 `main`，否则个人文件会带进去。

## 两台机器

| 机器                          | 用途                                                       |
| ----------------------------- | ---------------------------------------------------------- |
| mac                           | 编辑代码、提交推送、tmux 控制、浏览器查看                  |
| JensynPC（WSL，SSH 别名同名） | 运行、测试、启动服务；项目在 `~/PaperReTeX`，环境 `.venv/` |

mac 上不装依赖、不跑测试。改动推送后在 JensynPC 上 `git pull`，核对提交号再运行：

```sh
ssh JensynPC 'cd ~/PaperReTeX && git pull --ff-only && git log --oneline -1'
ssh JensynPC 'cd ~/PaperReTeX && .venv/bin/python -m pytest -q'
```

`samples/` 下的四篇样例 PDF（EMS、Kobsik、Light-SQ、ResFit）不在 Git 中，只在
JensynPC 上；`scripts/benchmark.py`、`scripts/reverse_samples.py` 和
`tests/test_converter.py` 的两个集成测试依赖它们。

## 在 JensynPC 启动网页版

`codex` 装在 nvm 下，非交互 shell 的 `PATH` 里没有它，`paperretex doctor` 会显示
`"codex": null`。启动前先加进 `PATH`。服务放在 JensynPC 自己的 tmux 会话里，断开
SSH 不受影响：

```sh
ssh JensynPC
tmux new-session -d -s paperretex-web -c ~/PaperReTeX
tmux send-keys -t paperretex-web 'export PATH=~/.nvm/versions/node/v22.23.1/bin:$PATH && .venv/bin/python app.py --port 8765' Enter
```

`codex` 使用 ChatGPT 登录，经 JensynPC 自己的代理 `127.0.0.1:7890` 出网。JensynPC
上没有 `claude` CLI，网页里只能选 Codex。任务和结果在 JensynPC 的
`~/.cache/paperretex/web/`。

## 从 mac 打开（tailnet 端口转发）

服务只监听 JensynPC 的 `127.0.0.1`，不要用 `--host 0.0.0.0` 暴露（无登录验证）。用
`leader-forward` 经 SSH（走 tailnet）转发到 mac，在 mac 的 tmux pane 里运行：

```sh
~/.local/bin/leader-forward plan --title 'PaperReTeX 网页' --url 'http://localhost:8765/' -- ssh -N -L 127.0.0.1:8765:127.0.0.1:8765 JensynPC
~/.local/bin/leader-forward add  --title 'PaperReTeX 网页' --url 'http://localhost:8765/' -- ssh -N -L 127.0.0.1:8765:127.0.0.1:8765 JensynPC
curl --noproxy '*' -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8765/   # 期望 200
```

然后在 mac 打开 <http://localhost:8765/>，或 `F23 → m → p` 从端口页进入。服务不支持
`HEAD`，`curl -I` 返回 501 也说明已连到服务。用完：

```sh
~/.local/bin/leader-forward list
~/.local/bin/leader-forward remove --id <id>
ssh JensynPC 'tmux kill-session -t paperretex-web'
```
