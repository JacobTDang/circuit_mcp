import AppKit
import WebKit

/// The desk in a WKWebView, with the four browser features the page relies on.
final class WebWindow: NSObject, WKUIDelegate, WKNavigationDelegate {
    let webView: WKWebView
    var onLoadFailure: ((Error) -> Void)?
    private var serverRoot: URL?

    override init() {
        let configuration = WKWebViewConfiguration()
        configuration.websiteDataStore = .default()  // persistent: localStorage survives relaunch
        webView = WKWebView(frame: .zero, configuration: configuration)
        super.init()
        webView.uiDelegate = self
        webView.navigationDelegate = self
    }

    func load(port: Int) {
        let root = URL(string: "http://127.0.0.1:\(port)/")!
        serverRoot = root
        webView.load(URLRequest(url: root))
    }

    func reload() {
        webView.reload()
    }

    // <input type="file">
    func webView(_ webView: WKWebView, runOpenPanelWith parameters: WKOpenPanelParameters,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping ([URL]?) -> Void) {
        let panel = NSOpenPanel()
        panel.allowsMultipleSelection = parameters.allowsMultipleSelection
        panel.canChooseDirectories = parameters.allowsDirectories
        panel.canChooseFiles = true
        panel.begin { response in completionHandler(response == .OK ? panel.urls : nil) }
    }

    // confirm()
    func webView(_ webView: WKWebView, runJavaScriptConfirmPanelWithMessage message: String,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping (Bool) -> Void) {
        let alert = NSAlert()
        alert.messageText = message
        alert.addButton(withTitle: "OK")
        alert.addButton(withTitle: "Cancel")
        completionHandler(alert.runModal() == .alertFirstButtonReturn)
    }

    // alert()
    func webView(_ webView: WKWebView, runJavaScriptAlertPanelWithMessage message: String,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping () -> Void) {
        let alert = NSAlert()
        alert.messageText = message
        alert.runModal()
        completionHandler()
    }

    // target="_blank" opens in the default browser.
    func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration,
                 for navigationAction: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
        if let url = navigationAction.request.url { NSWorkspace.shared.open(url) }
        return nil
    }

    // Anything outside the server's origin opens in the default browser.
    func webView(_ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction,
                 decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        guard let url = navigationAction.request.url, let root = serverRoot else { return decisionHandler(.allow) }
        let sameOrigin = url.scheme == root.scheme && url.host == root.host && url.port == root.port
        if sameOrigin || ["about", "blob", "data"].contains(url.scheme ?? "") {
            return decisionHandler(.allow)
        }
        NSWorkspace.shared.open(url)
        decisionHandler(.cancel)
    }

    func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) {
        onLoadFailure?(error)
    }

    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
        onLoadFailure?(error)
    }
}
