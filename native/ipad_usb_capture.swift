import AppKit
import AVFoundation
import CoreImage
import CoreMediaIO
import Foundation

func enableScreenDevices() throws {
    var address = CMIOObjectPropertyAddress(
        mSelector: CMIOObjectPropertySelector(kCMIOHardwarePropertyAllowScreenCaptureDevices),
        mScope: CMIOObjectPropertyScope(kCMIOObjectPropertyScopeGlobal),
        mElement: CMIOObjectPropertyElement(kCMIOObjectPropertyElementMain))
    var allow: UInt32 = 1
    let status = CMIOObjectSetPropertyData(CMIOObjectID(kCMIOObjectSystemObject), &address,
        0, nil, UInt32(MemoryLayout.size(ofValue: allow)), &allow)
    if status != noErr { throw NSError(domain: "CMIO", code: Int(status)) }
}

func devices() -> [AVCaptureDevice] {
    let session = AVCaptureDevice.DiscoverySession(
        deviceTypes: [.external], mediaType: .muxed, position: .unspecified)
    return session.devices
}

final class FrameSink: NSObject, AVCaptureVideoDataOutputSampleBufferDelegate {
    let destination: URL
    let done = DispatchSemaphore(value: 0)
    var error: Error?
    var captured = false
    init(_ destination: URL) { self.destination = destination }
    func captureOutput(_ output: AVCaptureOutput, didOutput buffer: CMSampleBuffer,
                       from connection: AVCaptureConnection) {
        guard !captured, let pixels = CMSampleBufferGetImageBuffer(buffer) else { return }
        captured = true
        let image = CIImage(cvPixelBuffer: pixels)
        let context = CIContext()
        guard let cg = context.createCGImage(image, from: image.extent),
              let png = NSBitmapImageRep(cgImage: cg).representation(using: .png, properties: [:]) else {
            error = NSError(domain: "Capture", code: 2,
                            userInfo: [NSLocalizedDescriptionKey: "Could not encode PNG"])
            done.signal(); return
        }
        do { try png.write(to: destination, options: .atomic) } catch { self.error = error }
        done.signal()
    }
}

do {
    try enableScreenDevices()
    Thread.sleep(forTimeInterval: 0.7)
    let found = devices()
    if CommandLine.arguments.contains("--list") {
        let values = found.map { ["name": $0.localizedName, "id": $0.uniqueID] }
        let data = try JSONSerialization.data(withJSONObject: values, options: [.sortedKeys])
        print(String(data: data, encoding: .utf8)!)
        exit(0)
    }
    guard let outputIndex = CommandLine.arguments.firstIndex(of: "--output"),
          outputIndex + 1 < CommandLine.arguments.count else {
        throw NSError(domain: "Usage", code: 1,
                      userInfo: [NSLocalizedDescriptionKey: "Use --list or --output PATH"])
    }
    guard let device = found.first else {
        throw NSError(domain: "Capture", code: 3,
                      userInfo: [NSLocalizedDescriptionKey: "No trusted USB iPad screen device found"])
    }
    let session = AVCaptureSession()
    let input = try AVCaptureDeviceInput(device: device)
    guard session.canAddInput(input) else { throw NSError(domain: "Capture", code: 4) }
    session.addInput(input)
    let video = AVCaptureVideoDataOutput()
    video.alwaysDiscardsLateVideoFrames = true
    let sink = FrameSink(URL(fileURLWithPath: CommandLine.arguments[outputIndex + 1]))
    video.setSampleBufferDelegate(sink, queue: DispatchQueue(label: "ipad-frame"))
    guard session.canAddOutput(video) else { throw NSError(domain: "Capture", code: 5) }
    session.addOutput(video)
    session.startRunning()
    let result = sink.done.wait(timeout: .now() + 10)
    session.stopRunning()
    if result == .timedOut { throw NSError(domain: "Capture", code: 6,
        userInfo: [NSLocalizedDescriptionKey: "Timed out waiting for an unlocked iPad frame"]) }
    if let error = sink.error { throw error }
    print("{\"ok\":true,\"device\":\"(device.localizedName)\"}")
} catch {
    fputs("{\"ok\":false,\"error\":\"(error.localizedDescription)\"}\n", stderr)
    exit(1)
}
