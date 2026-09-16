import AppKit
import WebKit

/// The desk in a WKWebView, with the four browser features the page relies on.
final class WebWindow: NSObject, WKUIDelegate, WKNavigationDelegate {
    /// A policy cancel is reported as a frame load interrupted in WebKit's own domain, which has
    /// no symbol in the SDK.
    private static let webKitErrorDomain = "WebKitErrorDomain"
    private static let frameLoadInterrupted = 102

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

    /// False when there is no page to go back to, so a caller that uncovers the desk on a reload
    /// does not uncover an empty one.
    var canReload: Bool { webView.url != nil || serverRoot != nil }

    /// Loads the page again. A navigation that failed while it was still provisional leaves the
    /// view with no URL at all, and `reload()` on that does nothing, so the server's own root is
    /// what a retry after a failure loads.
    @discardableResult
    func reload() -> Bool {
        if webView.url != nil {
            webView.reload()
            return true
        }
        guard let serverRoot else { return false }
        webView.load(URLRequest(url: serverRoot))
        return true
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
        if let url = navigationAction.request.url { openOutside(url) }
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
        openOutside(url)
        decisionHandler(.cancel)
    }

    /// Hands a URL the page chose to the rest of the Mac. Only the two schemes a link can
    /// reasonably mean are handed over: `file:` would open anything the user can read, and a
    /// custom scheme would launch another application, both on the page's say-so.
    private func openOutside(_ url: URL) {
        guard let scheme = url.scheme?.lowercased(), scheme == "http" || scheme == "https" else {
            NSLog("PrepPal did not open %@: only http and https links leave the desk.", url.absoluteString)
            return
        }
        NSWorkspace.shared.open(url)
    }

    func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) {
        report(error)
    }

    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
        report(error)
    }

    /// A cancelled or interrupted navigation is not a failure the user can act on: a redirect, a
    /// load started over the top of an older one, and the off-origin link that `decidePolicyFor`
    /// hands to the browser all end this way. Covering the desk for one of those traps the user
    /// behind a full-screen error whose only way out is a slow restart.
    private func report(_ error: Error) {
        let error = error as NSError
        let cancelled = (error.domain == NSURLErrorDomain && error.code == NSURLErrorCancelled)
            || (error.domain == Self.webKitErrorDomain && error.code == Self.frameLoadInterrupted)
        guard !cancelled else {
            // Not shown, but not dropped either: a page that cancels its own loads in a way the
            // user does notice leaves a trail here.
            NSLog("PrepPal: a navigation was cancelled (%@ %ld); the desk was left as it was.",
                  error.domain, error.code)
            return
        }
        onLoadFailure?(error)
    }
}
