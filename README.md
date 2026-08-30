# 飞书 × OpenAI 聊天机器人

一个零第三方依赖的 Python 服务。在飞书中私聊机器人，或在群里 `@机器人`，机器人会调用 OpenAI Responses API 并回复原消息。

## 1. 创建飞书应用

1. 打开[飞书开放平台](https://open.feishu.cn/app)，创建“企业自建应用”。
2. 在“添加应用能力”中添加“机器人”。
3. 在“权限管理”开通：
   - `im:message`（获取与发送单聊、群组消息）
   - 如果控制台拆分展示，再开通“以应用身份发消息”和“接收群聊中 @ 机器人消息”。
4. 创建版本并发布；把应用可用范围设为需要使用机器人的成员。

## 2. 配置并运行

```bash
cd feishu-openai-bot
cp .env.example .env
# 编辑 .env，填入飞书 App ID/App Secret/Verification Token 和 OPENAI_API_KEY
chmod +x run.sh
./run.sh
```

检查服务：

```bash
curl http://127.0.0.1:8000/health
```

## 3. 暴露 HTTPS 地址

飞书必须访问一个公网 HTTPS 地址。可将本服务部署到任意支持 Python 的平台，或在本机使用 Cloudflare Tunnel/ngrok：

```text
https://你的域名/feishu/events
```

生产环境建议由反向代理提供 HTTPS，并以守护进程运行 `./run.sh`。

## 4. 配置事件订阅

1. 飞书开发者后台 → “事件与回调” → “事件配置”。
2. 订阅方式选“将事件发送至开发者服务器”。
3. 请求地址填 `https://你的域名/feishu/events`。
4. 把页面显示的 Verification Token 填入 `.env`。
5. 添加事件“接收消息” `im.message.receive_v1`。
6. 此零依赖版本请把 Encrypt Key 留空，依靠 Verification Token 与 HTTPS 校验请求。若你的网关会解密事件体，可把 Encrypt Key 填入 `.env`，服务还会校验 `X-Lark-Signature`；服务本身不解密 `encrypt` 字段。
7. 重新发布应用版本。

现在私聊机器人即可对话；群聊中需要 `@机器人`。上下文按会话保存在进程内，重启后清空。

## 安全提示

- `.env` 不要提交到 Git，也不要把 App Secret 或 API Key 发到群聊。
- 建议限制飞书应用可用范围，并在部署平台设置环境变量。
- 若需长期上下文、多实例部署或审计，请把内存历史与事件去重替换为 Redis/数据库。
