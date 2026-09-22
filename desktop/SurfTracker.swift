import Cocoa
import UniformTypeIdentifiers
import WebKit

final class VideoDropWebView: WKWebView {
    var onVideoURL: ((URL) -> Void)?

    override init(frame: CGRect, configuration: WKWebViewConfiguration) {
        super.init(frame: frame, configuration: configuration)
        registerForDraggedTypes([.fileURL])
    }

    required init?(coder: NSCoder) {
        fatalError("init(coder:) has not been implemented")
    }

    override func draggingEntered(_ sender: NSDraggingInfo) -> NSDragOperation {
        videoURL(from: sender.draggingPasteboard) == nil ? [] : .link
    }

    override func performDragOperation(_ sender: NSDraggingInfo) -> Bool {
        guard let url = videoURL(from: sender.draggingPasteboard) else { return false }
        onVideoURL?(url)
        return true
    }

    private func videoURL(from pasteboard: NSPasteboard) -> URL? {
        let options: [NSPasteboard.ReadingOptionKey: Any] = [.urlReadingFileURLsOnly: true]
        guard let urls = pasteboard.readObjects(forClasses: [NSURL.self], options: options) as? [URL] else {
            return nil
        }
        let extensions = Set(["mp4", "mov", "m4v", "avi", "mkv"])
        return urls.first { extensions.contains($0.pathExtension.lowercased()) }
    }
}

final class AppDelegate: NSObject, NSApplicationDelegate, WKScriptMessageHandler, WKNavigationDelegate {
    private var window: NSWindow!
    private var webView: VideoDropWebView!
    private let appURL = URL(string: "http://127.0.0.1:8765/")!

    func applicationDidFinishLaunching(_ notification: Notification) {
        let configuration = WKWebViewConfiguration()
        configuration.userContentController.add(self, name: "selectVideo")
        configuration.userContentController.add(self, name: "saveVideo")
        webView = VideoDropWebView(frame: .zero, configuration: configuration)
        webView.navigationDelegate = self
        webView.onVideoURL = { [weak self] url in self?.sendVideo(url) }

        window = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 1240, height: 800),
            styleMask: [.titled, .closable, .miniaturizable, .resizable],
            backing: .buffered,
            defer: false
        )
        window.title = "Surf Tracker"
        window.minSize = NSSize(width: 820, height: 620)
        window.contentView = webView
        window.center()
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
        webView.load(URLRequest(url: appURL))
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        true
    }

    func userContentController(
        _ userContentController: WKUserContentController,
        didReceive message: WKScriptMessage
    ) {
        if message.name == "selectVideo" {
            let panel = NSOpenPanel()
            panel.allowedContentTypes = [.movie]
            panel.allowsMultipleSelection = false
            panel.canChooseDirectories = false
            panel.beginSheetModal(for: window) { [weak self] response in
                guard response == .OK, let url = panel.url else { return }
                self?.sendVideo(url)
            }
        } else if message.name == "saveVideo" {
            let panel = NSSavePanel()
            panel.allowedContentTypes = [.mpeg4Movie]
            panel.canCreateDirectories = true
            panel.isExtensionHidden = false
            if let body = message.body as? [String: Any],
               let suggestedName = body["suggestedName"] as? String {
                panel.nameFieldStringValue = URL(fileURLWithPath: suggestedName).lastPathComponent
            }
            panel.beginSheetModal(for: window) { [weak self] response in
                self?.sendExportDestination(response == .OK ? panel.url : nil)
            }
        }
    }

    func webView(
        _ webView: WKWebView,
        decidePolicyFor navigationAction: WKNavigationAction,
        decisionHandler: @escaping (WKNavigationActionPolicy) -> Void
    ) {
        guard let url = navigationAction.request.url else {
            decisionHandler(.cancel)
            return
        }
        if url.host == appURL.host && url.port == appURL.port {
            decisionHandler(.allow)
        } else {
            NSWorkspace.shared.open(url)
            decisionHandler(.cancel)
        }
    }

    private func sendVideo(_ url: URL) {
        guard let data = try? JSONSerialization.data(withJSONObject: ["path": url.path]),
              let json = String(data: data, encoding: .utf8) else { return }
        let script = "window.dispatchEvent(new CustomEvent('surf-native-file', { detail: \(json) }));"
        webView.evaluateJavaScript(script)
    }

    private func sendExportDestination(_ url: URL?) {
        let detail: [String: Any] = url.map { ["path": $0.path] } ?? ["cancelled": true]
        guard let data = try? JSONSerialization.data(withJSONObject: detail),
              let json = String(data: data, encoding: .utf8) else { return }
        let script = "window.dispatchEvent(new CustomEvent('surf-native-export', { detail: \(json) }));"
        webView.evaluateJavaScript(script)
    }
}

let application = NSApplication.shared
let delegate = AppDelegate()
application.setActivationPolicy(.regular)
application.delegate = delegate
application.run()
