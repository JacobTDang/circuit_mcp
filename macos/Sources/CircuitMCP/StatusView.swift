import AppKit

/// Covers the web view while the server starts or data is imported, and explains any failure with
/// the log tail.
final class StatusView: NSView {
    var onOpenLog: (() -> Void)?
    var onRestart: (() -> Void)?
    var onQuit: (() -> Void)?
    var onCancel: (() -> Void)?

    private let spinner = NSProgressIndicator()
    private let progressBar = NSProgressIndicator()
    private let cancelButton = NSButton()
    private let titleLabel = NSTextField(wrappingLabelWithString: "")
    private let detailLabel = NSTextField(wrappingLabelWithString: "")
    private let logView = NSTextView()
    private let logScroll = NSScrollView()
    private let buttonRow = NSStackView()

    init() {
        super.init(frame: .zero)
        wantsLayer = true
        layer?.backgroundColor = NSColor.windowBackgroundColor.cgColor

        spinner.style = .spinning
        progressBar.style = .bar
        progressBar.minValue = 0
        progressBar.maxValue = 1
        cancelButton.title = "Cancel Import"
        cancelButton.bezelStyle = .rounded
        cancelButton.target = self
        cancelButton.action = #selector(cancelPressed)
        titleLabel.font = .systemFont(ofSize: 17, weight: .semibold)
        detailLabel.textColor = .secondaryLabelColor
        detailLabel.isSelectable = true

        logView.isEditable = false
        logView.font = .monospacedSystemFont(ofSize: 11, weight: .regular)
        logScroll.documentView = logView
        logScroll.hasVerticalScroller = true
        logScroll.borderType = .bezelBorder
        logView.autoresizingMask = [.width]

        let openLog = NSButton(title: "Open Log", target: self, action: #selector(openLogPressed))
        let restart = NSButton(title: "Restart", target: self, action: #selector(restartPressed))
        let quit = NSButton(title: "Quit", target: self, action: #selector(quitPressed))
        restart.keyEquivalent = "\r"
        [openLog, restart, quit].forEach(buttonRow.addArrangedSubview)

        let column = NSStackView(views: [spinner, titleLabel, progressBar, detailLabel, logScroll, buttonRow, cancelButton])
        column.orientation = .vertical
        column.alignment = .leading
        column.spacing = 12
        column.translatesAutoresizingMaskIntoConstraints = false
        addSubview(column)
        NSLayoutConstraint.activate([
            column.leadingAnchor.constraint(equalTo: leadingAnchor, constant: 32),
            column.trailingAnchor.constraint(equalTo: trailingAnchor, constant: -32),
            column.centerYAnchor.constraint(equalTo: centerYAnchor),
            logScroll.widthAnchor.constraint(equalTo: column.widthAnchor),
            logScroll.heightAnchor.constraint(equalToConstant: 260),
            progressBar.widthAnchor.constraint(equalTo: column.widthAnchor),
        ])
        progressBar.isHidden = true
        cancelButton.isHidden = true
    }

    required init?(coder: NSCoder) {
        fatalError("StatusView is built in code")
    }

    func showLoading(_ message: String) {
        isHidden = false
        spinner.isHidden = false
        spinner.startAnimation(nil)
        titleLabel.stringValue = message
        detailLabel.isHidden = true
        logScroll.isHidden = true
        buttonRow.isHidden = true
        hideProgress()
    }

    /// A bar rather than the spinner: a copy can take minutes, and the student has to be able to
    /// tell a slow one from a stuck one -- and to stop either.
    /// - Parameter fraction: nil while there is nothing yet to measure against.
    func showProgress(_ title: String, detail: String, fraction: Double?, canCancel: Bool) {
        isHidden = false
        spinner.stopAnimation(nil)
        spinner.isHidden = true
        titleLabel.stringValue = title
        detailLabel.stringValue = detail
        detailLabel.isHidden = false
        progressBar.isHidden = false
        if let fraction {
            progressBar.stopAnimation(nil)
            progressBar.isIndeterminate = false
            progressBar.doubleValue = fraction
        } else {
            progressBar.isIndeterminate = true
            progressBar.startAnimation(nil)
        }
        logScroll.isHidden = true
        buttonRow.isHidden = true
        cancelButton.isHidden = false
        cancelButton.isEnabled = canCancel
    }

    func showError(title: String, detail: String, logTail: String?) {
        isHidden = false
        spinner.stopAnimation(nil)
        spinner.isHidden = true
        titleLabel.stringValue = title
        detailLabel.stringValue = detail
        detailLabel.isHidden = false
        logView.string = logTail ?? ""
        logScroll.isHidden = logTail == nil
        buttonRow.isHidden = false
        hideProgress()
    }

    func hide() {
        spinner.stopAnimation(nil)
        hideProgress()
        isHidden = true
    }

    private func hideProgress() {
        progressBar.stopAnimation(nil)
        progressBar.isHidden = true
        cancelButton.isHidden = true
    }

    @objc private func openLogPressed() { onOpenLog?() }
    @objc private func restartPressed() { onRestart?() }
    @objc private func quitPressed() { onQuit?() }
    @objc private func cancelPressed() { onCancel?() }
}
