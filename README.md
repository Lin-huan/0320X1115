# ONES 批量邀请激活工具

这是一个 Python 命令行工具，流程是：

1. 用管理员账号登录 ONES
2. 批量随机生成邮箱并发起邀请
3. 读取 invitation 列表里的 `code` / `invite_link`
4. 用邀请码批量激活成员

脚本文件是 [ones_batch_inviter.py](/Users/linhuan/Documents/0320x1115/ones_batch_inviter.py)。

## 运行要求

- Python 3.9+
- 系统安装了 `openssl`
- 不依赖第三方 Python 包

## 常用命令

先设置管理员账号：

```bash
export ONES_ADMIN_EMAIL='shiyong01@ones.cn'
export ONES_ADMIN_PASSWORD='Aa123456789'
```

邀请并立即激活 10 个成员：

```bash
python3 ones_batch_inviter.py run --count 10
```

只邀请，不激活：

```bash
python3 ones_batch_inviter.py invite --count 10
```

基于邀请码文件批量激活：

```bash
python3 ones_batch_inviter.py activate --input outputs/invite-result.csv
```

## 常用参数

- `--count`: 邀请人数
- `--admin-email`: 管理员邮箱
- `--admin-password`: 管理员密码
- `--member-password`: 被邀请成员激活后的统一密码，默认 `Aa123456789`
- `--email-prefix`: 随机邮箱前缀，默认 `ones-batch`
- `--email-domain`: 随机邮箱域名，默认 `example.com`
- `--output`: 结果 CSV 文件路径

不传 `--output` 时，脚本默认覆盖固定文件：

- `invite` -> `outputs/invite-result.csv`
- `activate` -> `outputs/activate-result.csv`
- `run` -> `outputs/run-result.csv`

## 环境变量

脚本会优先读取当前项目目录下的 `.env` 文件，再读取终端环境变量；命令行参数优先级最高。

- `ONES_BASE_URL`
- `ONES_IDENTITY_BASE_URL`
- `ONES_PROJECT_BASE_URL`
- `ONES_ORG_UUID`
- `ONES_TEAM_UUID`
- `ONES_REGION_UUID`
- `ONES_PROJECT_APP_PATH`
- `ONES_PROJECT_API_PREFIX`
- `ONES_LOGIN_ENCRYPTION_SOURCE`
- `ONES_ADMIN_EMAIL`
- `ONES_ADMIN_PASSWORD`
- `ONES_MEMBER_PASSWORD`
- `ONES_EMAIL_PREFIX`
- `ONES_EMAIL_DOMAIN`
- `ONES_FETCH_RETRIES`
- `ONES_FETCH_INTERVAL`

默认值已经按你这次提供的环境预置：

- `ONES_BASE_URL=https://x1115-k3s-11.k3s-dev.myones.net`
- `ONES_ORG_UUID=M81VR3T9`
- `ONES_TEAM_UUID=CexqToCd`
- `ONES_REGION_UUID=default`

## 项目配置文件

可以直接编辑当前项目下的 [.env](/Users/linhuan/Documents/0320x1115/.env)：

```bash
ONES_BASE_URL='https://x1115-k3s-11.k3s-dev.myones.net'
ONES_ORG_UUID='M81VR3T9'
ONES_TEAM_UUID='CexqToCd'
ONES_REGION_UUID='default'
ONES_ADMIN_EMAIL='shiyong01@ones.cn'
ONES_ADMIN_PASSWORD='Aa123456789'
ONES_MEMBER_PASSWORD='Aa123456789'
```

如果某套环境的认证域名和项目接口域名不一致，可以额外配置：

```bash
ONES_IDENTITY_BASE_URL='https://identity.example.com'
ONES_PROJECT_BASE_URL='https://project.example.com'
ONES_LOGIN_ENCRYPTION_SOURCE='auto'
ONES_PROJECT_APP_PATH='/project'
ONES_PROJECT_API_PREFIX='/project/api/project'
```

说明：

- `ONES_BASE_URL`: 浏览器入口地址，用于 `Origin`、`Referer` 和授权回调地址
- `ONES_IDENTITY_BASE_URL`: 登录、授权、token 交换所在域名
- `ONES_PROJECT_BASE_URL`: 项目接口所在域名
- `ONES_LOGIN_ENCRYPTION_SOURCE`: `auto` 时先试 `identity/api/encryption_cert`，失败再试 `project auth/login_support`
- `ONES_PROJECT_API_PREFIX`: 业务 API 前缀，默认 `/project/api/project`

改完后直接执行：

```bash
python3 ones_batch_inviter.py run --count 20
```

## 输出结果

脚本会输出一个 CSV，包含：

- `email`
- `invite_code`
- `invite_link`
- `activated`
- `activation_status`
- `activation_message`

如果 `run` 命令里有部分邮箱没有及时查到邀请码，命令会返回非 0 状态码，并把已查到的结果照常写入 CSV。
