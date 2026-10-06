import AppKit
import CircuitMCPCore
import WebKit

/// The desk in a WKWebView, with the four browser features the page relies on.
final class WebWindow: NSObject, WKUIDelegate, WKNavigationDelegate, WKDownloadDelegate {
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
    // A download (Save PNG) has to be answered `.download` or WebKit shows the
    // blob as the page. The choice itself is `NavigationPolicy`, so it can be
    // tested without a web view.
    func webView(_ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction,
                 decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        // No URL yet is WebKit's own initial load. Cancelling it leaves a blank desk.
        guard let url = navigationAction.request.url, let root = serverRoot else { return decisionHandler(.allow) }
        // A nil target frame is a new window. Treating it as the desk is what
        // stops a blob from opening over the page when it is not a download.
        let isMainFrame = navigationAction.targetFrame?.isMainFrame ?? true
        switch NavigationPolicy.decide(url: url, serverRoot: root,
                                        shouldPerformDownload: navigationAction.shouldPerformDownload,
                                        isMainFrame: isMainFrame) {
        case .allow:
            decisionHandler(.allow)
        case .download:
            decisionHandler(.download)
        case .openOutside:
            openOutside(url)
            decisionHandler(.cancel)
        case .cancel:
            if let scheme = url.scheme?.lowercased(), scheme != "blob", scheme != "data" {
                NSLog("CircuitMCP did not open %@: only http and https links leave the desk.", url.absoluteString)
            }
            decisionHandler(.cancel)
        }
    }

    /// `.download` does nothing until something adopts the download. Without
    /// this, WebKit drops the file on the floor after the policy returns.
    func webView(_ webView: WKWebView, navigationAction: WKNavigationAction, didBecome download: WKDownload) {
        download.delegate = self
    }

    /// The suggested name is what Save PNG put in the `<a download>` attribute
    /// (`schematic-<id>.png`). A cancelled panel passes nil, which cancels the
    /// download rather than writing somewhere the user did not choose.
    func download(_ download: WKDownload, decideDestinationUsing response: URLResponse,
                  suggestedFilename: String, completionHandler: @escaping (URL?) -> Void) {
        let panel = NSSavePanel()
        panel.canCreateDirectories = true
        panel.isExtensionHidden = false
        panel.nameFieldStringValue = suggestedFilename
        let finish: (NSApplication.ModalResponse) -> Void = { result in
            completionHandler(result == .OK ? panel.url : nil)
        }
        if let window = webView.window {
            panel.beginSheetModal(for: window, completionHandler: finish)
        } else {
            finish(panel.runModal())
        }
    }

    /// A failed download used to vanish: the policy had already cancelled the
    /// navigation, so nothing reached `onLoadFailure`. The user has to hear it.
    func download(_ download: WKDownload, didFailWithError error: Error, resumeData: Data?) {
        NSLog("CircuitMCP: a download failed: %@ (resume data %ld bytes)",
              error.localizedDescription, resumeData?.count ?? 0)
        let alert = NSAlert()
        alert.messageText = "The file was not saved."
        alert.informativeText = error.localizedDescription
        alert.runModal()
    }

    /// Hands a URL the page chose to the rest of the Mac. Only the two schemes a link can
    /// reasonably mean are handed over: `file:` would open anything the user can read, and a
    /// custom scheme would launch another application, both on the page's say-so.
    private func openOutside(_ url: URL) {
        guard let scheme = url.scheme?.lowercased(), scheme == "http" || scheme == "https" else {
            NSLog("CircuitMCP did not open %@: only http and https links leave the desk.", url.absoluteString)
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
            NSLog("CircuitMCP: a navigation was cancelled (%@ %ld); the desk was left as it was.",
                  error.domain, error.code)
            return
        }
        onLoadFailure?(error)
    }
}
