// Fibona on iOS: the app's side of the home-screen widget, reached from JavaScript as ObjC.FBWidget. WidgetKit is
// Swift-only, so main.m cannot call it.
import Foundation
import WidgetKit

@objc(FBWidget) class FBWidget: NSObject {
  // The folder the widget shows PNGs from, in the app group it shares with the app; nil without the app group.
  @objc static func directory() -> String? {
    let group = "group." + Bundle.main.bundleIdentifier!
    guard let url = FileManager.default.containerURL(forSecurityApplicationGroupIdentifier: group)?
      .appendingPathComponent("widget") else { return nil }
    try? FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
    return url.path
  }

  // Redraws the widget from the folder.
  @objc static func reload() {
    WidgetCenter.shared.reloadAllTimelines()
  }
}
