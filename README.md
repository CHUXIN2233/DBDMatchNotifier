# DBD Match Notifier

一个 Windows 后台工具：通过观察《Dead by Daylight》进程的 UDP endpoint
变化，判断匹配成功，并使用微信 ClawBot/iLink Bot 发送提醒。

## 特性

- 不读取或修改游戏进程内存，不注入 DLL，不 Hook 游戏函数。
- 以 DBD 进程启动时的 UDP endpoint 集合作为 baseline。
- baseline 之外的新 endpoint 连续存在至少 2.5 秒后触发一次匹配事件。
- 只跟踪本轮 candidate；candidate 消失后重新武装，不要求旧 endpoint 清零。
- DBD 退出时清空状态，下一次启动重新建立 baseline。
- 微信认证状态和运行日志统一保存到 `%LOCALAPPDATA%\\DBDMatchNotifier\\`。
- 单实例运行，避免重复发送通知。

## 使用已发布 EXE

下载 GitHub Release 中的 `DBDMatchNotifier.exe`，放到任意可写目录后双击运行。
EXE 不依赖本机安装 Python，也不依赖 EXE 所在目录写权限。

首次使用需要先完成微信 ClawBot 二维码认证。推荐使用源码方式执行一次登录：

```powershell
python dbd_wechat_notifier.py login
```

用手机微信扫描二维码并确认授权，然后启动提醒器：

```powershell
python dbd_match_network_notifier.py
```

认证信息会保存到：

```text
%LOCALAPPDATA%\\DBDMatchNotifier\\wechat_state.json
```

正式 EXE 会自动读取同一个状态文件。调试时可以使用控制台版本或关闭微信发送：

```powershell
DBDMatchNotifierConsole.exe --no-wechat
```

## 日志

日志位于 `%LOCALAPPDATA%\\DBDMatchNotifier\\`，包括状态机日志、匹配事件日志和
微信通知日志。日志可能包含本地 UDP 端口和时间戳，不应提交到公开仓库。

## 从源码运行

运行环境为 Windows、Python 3.10+ 和 PowerShell。运行时只使用 Python 标准库；
PyInstaller 仅用于打包：

```powershell
python dbd_wechat_notifier.py login
python dbd_match_network_notifier.py
```

## 本地构建

```powershell
python -m pip install -r requirements-build.txt
python -m PyInstaller --clean DBDMatchNotifier.spec
```

输出为 `dist\\DBDMatchNotifier.exe`。控制台调试版可额外构建：

```powershell
python -m PyInstaller --clean --onefile --name DBDMatchNotifierConsole `
  --version-file version_info.txt dbd_match_network_notifier.py
```

## GitHub Release

推送形如 `v0.1.0` 的 tag 后，GitHub Actions 会在 Windows runner 上构建
`DBDMatchNotifier.exe`，并自动创建对应 GitHub Release 上传该文件。

## 微信协议说明

通知层使用腾讯 `Tencent/openclaw-weixin` 所采用的 iLink Bot 接口，包括二维码
登录、`getupdates` 会话上下文保存和 `sendmessage`。微信接口或 ClawBot 服务端
行为变化时，可能需要更新通知模块；UDP 检测核心与通知层保持解耦。

## 免责声明

这是一个个人辅助工具。请遵守游戏、平台和微信服务的相关规则；项目不保证第三方
服务长期兼容，也不提供任何绕过加密或反作弊的功能。
