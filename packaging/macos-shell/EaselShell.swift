// Native window for the loopback workbench (decision 0008). With arguments it
// forwards to easel-launcher, so Stop Easel.command and verify_portable keep working.
import AppKit
import WebKit

let environment = ProcessInfo.processInfo.environment
let launcher = Bundle.main.executableURL!.deletingLastPathComponent().appendingPathComponent("easel-launcher").path

if CommandLine.arguments.count > 1 {
    let argv: [UnsafeMutablePointer<CChar>?] = ([launcher] + CommandLine.arguments.dropFirst()).map { strdup($0) } + [nil]
    execv(launcher, argv)
    perror("easel-launcher")
    exit(127)
}

let port = environment["EASEL_SHELL_PORT"] ?? "17868"
let workspace = environment["EASEL_SHELL_WORKSPACE"]
    ?? NSHomeDirectory() + "/Library/Application Support/Easel/workspaces/main"
let home = URL(string: "http://127.0.0.1:\(port)/")!
let statusURL = home.appendingPathComponent("api/update/status")
let logPath = NSHomeDirectory() + "/Library/Logs/Easel/workbench-\(port).log"

func isLocal(_ url: URL) -> Bool { url.host == "127.0.0.1" && url.port == Int(port) }
func isWeb(_ url: URL) -> Bool { url.scheme == "http" || url.scheme == "https" }

final class Shell: NSObject, NSApplicationDelegate, NSWindowDelegate, WKNavigationDelegate, WKUIDelegate, WKDownloadDelegate {
    var window: NSWindow!
    var webView: WKWebView!
    var popups: [NSWindow] = []
    var server: Process?
    var started = Date()

    func applicationDidFinishLaunching(_ note: Notification) {
        if openCurrentVersion() { return }
        buildMenu()
        let configuration = WKWebViewConfiguration()
        configuration.preferences.isElementFullscreenEnabled = true
        webView = makeWebView(configuration)
        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1400, height: 900),
                          styleMask: [.titled, .closable, .miniaturizable, .resizable], backing: .buffered, defer: false)
        window.title = "优创"
        window.minSize = NSSize(width: 900, height: 600)
        window.isReleasedWhenClosed = false
        window.contentView = webView
        window.center()
        window.setFrameAutosaveName("EaselMain")
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
        start()
    }

    // A Dock pin keeps pointing at the version it was made from; after an update the
    // current one must start, or the old code would serve the workspace.
    func openCurrentVersion() -> Bool {
        let app = Bundle.main.bundleURL.resolvingSymlinksInPath()
        let root = app.deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
        let current = root.appendingPathComponent("current/Easel/Easel.app")
        guard FileManager.default.fileExists(atPath: current.path), current.resolvingSymlinksInPath() != app else { return false }
        let target = current.resolvingSymlinksInPath()
        let running = NSRunningApplication.runningApplications(withBundleIdentifier: Bundle.main.bundleIdentifier ?? "")
        if let open = running.first(where: { $0.bundleURL?.resolvingSymlinksInPath() == target }) {
            open.activate()
            exit(0)
        }
        let configuration = NSWorkspace.OpenConfiguration()
        configuration.createsNewApplicationInstance = true  // Same bundle id as this process.
        configuration.environment = environment
        NSWorkspace.shared.openApplication(at: current, configuration: configuration) { _, _ in exit(0) }
        return true
    }

    func makeWebView(_ configuration: WKWebViewConfiguration) -> WKWebView {
        let view = WKWebView(frame: .zero, configuration: configuration)
        view.navigationDelegate = self
        view.uiDelegate = self
        view.isInspectable = true
        return view
    }

    // MARK: Server

    func start() {
        started = Date()
        showPage("正在启动优创…", detail: "")
        let process = Process()
        process.executableURL = URL(fileURLWithPath: launcher)
        process.arguments = ["--workspace", workspace, "--port", port, "--no-browser"]
        var values = environment
        values["EASEL_LOG_FILE"] = logPath
        // Apps started from Finder get a bare PATH, unlike Start Easel.command.
        values["PATH"] = "/opt/homebrew/bin:/usr/local/bin:" + (values["PATH"] ?? "/usr/bin:/bin:/usr/sbin:/sbin")
        process.environment = values
        do { try process.run() } catch {
            showPage("优创启动失败", detail: error.localizedDescription, retry: true)
            return
        }
        server = process
        waitForServer()
    }

    func waitForServer() {
        var request = URLRequest(url: statusURL)
        request.timeoutInterval = 2
        URLSession.shared.dataTask(with: request) { _, response, _ in
            DispatchQueue.main.async {
                if (response as? HTTPURLResponse)?.statusCode == 200 {
                    self.webView.load(URLRequest(url: home))
                } else if let server = self.server, !server.isRunning, server.terminationStatus != 0 {
                    self.showPage("优创启动失败", detail: "详情见日志：\(logPath)", retry: true)
                } else if Date().timeIntervalSince(self.started) > 120 {
                    self.showPage("优创启动超时", detail: "详情见日志：\(logPath)", retry: true)
                } else {
                    DispatchQueue.main.asyncAfter(deadline: .now() + 0.3) { self.waitForServer() }
                }
            }
        }.resume()
    }

    func showPage(_ title: String, detail: String, retry: Bool = false) {
        let link = retry ? "<p><a href=\"easel-shell://retry\">重试</a></p>" : ""
        let html = """
        <!doctype html><meta charset="utf-8"><style>:root{color-scheme:light dark}
        body{font:15px -apple-system;display:flex;flex-direction:column;align-items:center;justify-content:center;height:90vh;margin:0}
        h1{font-size:20px;font-weight:600}p{opacity:.7;margin:4px}</style><h1>\(title)</h1><p>\(detail)</p>\(link)
        """
        webView.loadHTMLString(html, baseURL: nil)
    }

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        guard webView != nil else { return .terminateNow }
        var request = URLRequest(url: statusURL)
        request.timeoutInterval = 2
        URLSession.shared.dataTask(with: request) { data, _, _ in
            let status = data.flatMap { try? JSONSerialization.jsonObject(with: $0) } as? [String: Any]
            DispatchQueue.main.async {
                guard let status else { sender.reply(toApplicationShouldTerminate: true); return }
                if status["busy"] as? Bool == true, !self.confirm("有任务正在运行，退出会中断它。确定退出吗？") {
                    sender.reply(toApplicationShouldTerminate: false)
                    return
                }
                self.stopServer { sender.reply(toApplicationShouldTerminate: true) }
            }
        }.resume()
        return .terminateLater
    }

    func stopServer(_ done: @escaping () -> Void) {
        // An open page holds streaming connections that would delay shutdown.
        webView.loadHTMLString("", baseURL: nil)
        popups.forEach { $0.close() }
        let process = Process()
        process.executableURL = URL(fileURLWithPath: launcher)
        process.arguments = ["--workspace", workspace, "--stop", "--no-browser"]
        var finished = false
        let finish = { DispatchQueue.main.async { if !finished { finished = true; done() } } }
        process.terminationHandler = { _ in finish() }
        do { try process.run() } catch { finish(); return }
        DispatchQueue.main.asyncAfter(deadline: .now() + 15) { finish() }
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { false }

    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows: Bool) -> Bool {
        if !hasVisibleWindows { window.makeKeyAndOrderFront(nil) }
        return true
    }

    // MARK: Navigation

    func webView(_ view: WKWebView, decidePolicyFor action: WKNavigationAction, decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        guard let url = action.request.url else { decisionHandler(.cancel); return }
        if url.scheme == "easel-shell" { decisionHandler(.cancel); start(); return }
        if action.shouldPerformDownload { decisionHandler(.download); return }
        let external = view === webView ? isWeb(url) && !isLocal(url) : false
        if external || !(isWeb(url) || ["about", "blob", "data"].contains(url.scheme ?? "")) {
            if action.targetFrame?.isMainFrame != false { NSWorkspace.shared.open(url); decisionHandler(.cancel); return }
        }
        decisionHandler(.allow)
    }

    func webView(_ view: WKWebView, decidePolicyFor response: WKNavigationResponse, decisionHandler: @escaping (WKNavigationResponsePolicy) -> Void) {
        let disposition = (response.response as? HTTPURLResponse)?.value(forHTTPHeaderField: "Content-Disposition") ?? ""
        decisionHandler(disposition.lowercased().hasPrefix("attachment") || !response.canShowMIMEType ? .download : .allow)
    }

    func webView(_ view: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
        let code = (error as NSError).code
        if view === webView, code != NSURLErrorCancelled, code != 102 {  // 102: handed to a download
            showPage("连接不上优创", detail: "服务可能已停止。", retry: true)
        }
    }

    func webViewWebContentProcessDidTerminate(_ view: WKWebView) { view.reload() }

    func webView(_ view: WKWebView, navigationAction: WKNavigationAction, didBecome download: WKDownload) { download.delegate = self }
    func webView(_ view: WKWebView, navigationResponse: WKNavigationResponse, didBecome download: WKDownload) { download.delegate = self }

    func download(_ download: WKDownload, decideDestinationUsing response: URLResponse, suggestedFilename: String, completionHandler: @escaping (URL?) -> Void) {
        let panel = NSSavePanel()
        panel.nameFieldStringValue = suggestedFilename
        panel.directoryURL = FileManager.default.urls(for: .downloadsDirectory, in: .userDomainMask).first
        guard panel.runModal() == .OK, let url = panel.url else { completionHandler(nil); return }
        try? FileManager.default.removeItem(at: url)  // The panel already asked; WKDownload refuses existing files.
        completionHandler(url)
    }

    func download(_ download: WKDownload, didFailWithError error: Error, resumeData: Data?) {
        if (error as NSError).code != NSURLErrorCancelled { show("下载失败：\(error.localizedDescription)") }
    }

    // MARK: Page UI

    func webView(_ view: WKWebView, createWebViewWith configuration: WKWebViewConfiguration, for action: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
        if let url = action.request.url, isWeb(url), !isLocal(url) {
            NSWorkspace.shared.open(url)
            return nil
        }
        // Central login opens about:blank in the click, then points it at the account site.
        let popup = makeWebView(configuration)
        let child = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1000, height: 760),
                             styleMask: [.titled, .closable, .miniaturizable, .resizable], backing: .buffered, defer: false)
        child.title = "优创"
        child.isReleasedWhenClosed = false
        child.contentView = popup
        child.delegate = self
        child.cascadeTopLeft(from: NSPoint(x: window.frame.minX + 24, y: window.frame.maxY - 24))
        child.makeKeyAndOrderFront(nil)
        popups.append(child)
        return popup
    }

    func webViewDidClose(_ view: WKWebView) { view.window?.close() }

    func windowWillClose(_ note: Notification) { popups.removeAll { $0 === note.object as? NSWindow } }

    func webView(_ view: WKWebView, runJavaScriptAlertPanelWithMessage message: String, initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping () -> Void) {
        show(message)
        completionHandler()
    }

    func webView(_ view: WKWebView, runJavaScriptConfirmPanelWithMessage message: String, initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping (Bool) -> Void) {
        completionHandler(confirm(message))
    }

    func webView(_ view: WKWebView, runOpenPanelWith parameters: WKOpenPanelParameters, initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping ([URL]?) -> Void) {
        let panel = NSOpenPanel()
        panel.allowsMultipleSelection = parameters.allowsMultipleSelection
        panel.canChooseDirectories = parameters.allowsDirectories
        completionHandler(panel.runModal() == .OK ? panel.urls : nil)
    }

    func show(_ message: String) {
        let alert = NSAlert()
        alert.messageText = message
        alert.addButton(withTitle: "好")
        alert.runModal()
    }

    func confirm(_ message: String) -> Bool {
        let alert = NSAlert()
        alert.messageText = message
        alert.addButton(withTitle: "确定")
        alert.addButton(withTitle: "取消")
        return alert.runModal() == .alertFirstButtonReturn
    }

    // MARK: Menu

    @objc func reloadPage() {
        if webView.url.map(isLocal) == true { webView.reload() } else { start() }
    }
    @objc func openInBrowser() { NSWorkspace.shared.open(home) }

    func buildMenu() {
        func menu(_ title: String, _ items: [NSMenuItem]) -> NSMenuItem {
            let holder = NSMenuItem(title: title, action: nil, keyEquivalent: "")
            holder.submenu = NSMenu(title: title)
            items.forEach { holder.submenu!.addItem($0) }
            return holder
        }
        func item(_ title: String, _ action: Selector, _ key: String = "") -> NSMenuItem {
            NSMenuItem(title: title, action: action, keyEquivalent: key)
        }
        let fullScreen = item("进入全屏幕", #selector(NSWindow.toggleFullScreen(_:)), "f")
        fullScreen.keyEquivalentModifierMask = [.command, .control]
        let main = NSMenu()
        [menu("优创", [item("关于优创", #selector(NSApplication.orderFrontStandardAboutPanel(_:))), .separator(),
                        item("隐藏优创", #selector(NSApplication.hide(_:)), "h"), .separator(),
                        item("退出优创", #selector(NSApplication.terminate(_:)), "q")]),
         menu("编辑", [item("撤销", Selector(("undo:")), "z"), item("重做", Selector(("redo:")), "Z"), .separator(),
                       item("剪切", #selector(NSText.cut(_:)), "x"), item("拷贝", #selector(NSText.copy(_:)), "c"),
                       item("粘贴", #selector(NSText.paste(_:)), "v"), item("全选", #selector(NSText.selectAll(_:)), "a")]),
         menu("显示", [item("重新加载", #selector(reloadPage), "r"), item("在浏览器中打开", #selector(openInBrowser)), .separator(), fullScreen]),
         menu("窗口", [item("最小化", #selector(NSWindow.performMiniaturize(_:)), "m"),
                       item("关闭窗口", #selector(NSWindow.performClose(_:)), "w")])].forEach { main.addItem($0) }
        NSApp.mainMenu = main
    }
}

let app = NSApplication.shared
let shell = Shell()
app.delegate = shell
app.setActivationPolicy(.regular)
app.run()
