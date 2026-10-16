// Fibona on iOS: the home-screen widget, an extension. SwiftUI is out of the agent's reach, so this only shows the
// PNGs the app draws into the folder of FBWidget.directory(), each named by the Unix time it starts showing, with the
// tap regions listed next to them. Fixed at build time.
import AppIntents
import SwiftUI
import UIKit
import WidgetKit

// The folder the app draws into, in the app group. The app's bundle identifier is this extension's without ".widget".
let folder = FileManager.default.containerURL(forSecurityApplicationGroupIdentifier:
  "group." + (Bundle.main.bundleIdentifier! as NSString).deletingPathExtension)?.appendingPathComponent("widget")

// A tap region from <image>.json; frame is [x, y, width, height] in fractions of the widget.
struct Region: Decodable {
  let name: String
  let frame: [Double]
  let image: String?  // Switched to on a tap, without opening the app. Without it, a tap opens the app.
}

struct Entry: TimelineEntry {
  let date: Date
  let image: UIImage?
  var regions: [Region] = []
}

func load(_ name: String, at date: Date) -> Entry {
  let url = folder?.appendingPathComponent(name)
  let regions = url.flatMap { try? Data(contentsOf: $0.deletingPathExtension().appendingPathExtension("json")) }
  return Entry(date: date, image: url.flatMap { UIImage(contentsOfFile: $0.path) },
               regions: regions.flatMap { try? JSONDecoder().decode([Region].self, from: $0) } ?? [])
}

// A tap on a region with an image: show that image until the next timed one is due, and log the tap for the app.
struct Tap: AppIntent {
  static var title: LocalizedStringResource = "Tap"
  @Parameter(title: "Region") var region: String
  @Parameter(title: "Image") var image: String
  init() {}
  init(region: String, image: String) {
    self.region = region
    self.image = image
  }
  func perform() async throws -> some IntentResult {
    guard let folder else { return .result() }
    let tap: [String: Any] = ["region": region, "image": image, "time": Date().timeIntervalSince1970]
    let data = try JSONSerialization.data(withJSONObject: tap)
    try data.write(to: folder.appendingPathComponent("state.json"))
    let line = data + Data("\n".utf8)
    if let handle = try? FileHandle(forWritingTo: folder.appendingPathComponent("taps.jsonl")) {
      handle.seekToEndOfFile()
      handle.write(line)
      try handle.close()
    } else {
      try line.write(to: folder.appendingPathComponent("taps.jsonl"))
    }
    return .result()  // WidgetKit redraws the widget after it.
  }
}

struct Provider: TimelineProvider {
  // The one showing now, then the timed ones still to come. ponytail: every image is loaded at once; fine for a few
  // days' worth, load lazily if the agent queues many.
  func entries() -> [Entry] {
    let now = Date()
    let names = (try? FileManager.default.contentsOfDirectory(atPath: folder?.path ?? "")) ?? []
    let timed = names.compactMap { name -> (Date, String)? in
      guard name.hasSuffix(".png"), let time = Double(name.dropLast(4)) else { return nil }
      return (Date(timeIntervalSince1970: time), name)
    }.sorted { $0.0 < $1.0 }
    var current = timed.last { $0.0 <= now }.map { load($0.1, at: $0.0) } ?? Entry(date: now, image: nil)
    // A tap since the timed image came due shows the region's image instead.
    if let data = folder.flatMap({ try? Data(contentsOf: $0.appendingPathComponent("state.json")) }),
       let state = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
       let image = state["image"] as? String, let time = state["time"] as? Double,
       Date(timeIntervalSince1970: time) >= current.date || current.image == nil {
      current = load(image, at: now)
    }
    return [current] + timed.filter { $0.0 > now }.map { load($0.1, at: $0.0) }
  }
  func placeholder(in context: Context) -> Entry { Entry(date: Date(), image: nil) }
  func getSnapshot(in context: Context, completion: @escaping (Entry) -> Void) { completion(entries()[0]) }
  // Redrawn when the app asks, through WidgetCenter, after a tap, and at each timed image's time.
  func getTimeline(in context: Context, completion: @escaping (Timeline<Entry>) -> Void) {
    completion(Timeline(entries: entries(), policy: .never))
  }
}

struct FibonaView: View {
  let entry: Entry
  var body: some View {
    GeometryReader { geometry in
      let size = geometry.size
      ZStack(alignment: .topLeading) {
        if entry.image == nil {
          Text("Ask Fibona to put something here.").font(.caption).foregroundStyle(Color(white: 0.84)).padding()
        }
        ForEach(entry.regions.indices, id: \.self) { i in
          let region = entry.regions[i], frame = region.frame + [0, 0, 0, 0]
          let target = Color.clear.contentShape(Rectangle())
          Group {
            if let image = region.image {
              Button(intent: Tap(region: region.name, image: image)) { target }.buttonStyle(.plain)
            } else {
              // ponytail: small widgets ignore Link, so there any tap opens the app without a region.
              Link(destination: URL(string: "fibona://widget/" + (region.name.addingPercentEncoding(
                withAllowedCharacters: .urlPathAllowed) ?? ""))!) { target }
            }
          }
          .frame(width: frame[2] * size.width, height: frame[3] * size.height)
          .offset(x: frame[0] * size.width, y: frame[1] * size.height)
        }
      }
    }
    .containerBackground(for: .widget) {
      if let image = entry.image {
        Image(uiImage: image).resizable().scaledToFill()
      } else {
        Color(red: 0.125, green: 0.125, blue: 0.18)
      }
    }
  }
}

@main
struct FibonaWidget: Widget {
  var body: some WidgetConfiguration {
    StaticConfiguration(kind: "Fibona", provider: Provider()) { FibonaView(entry: $0) }
      .contentMarginsDisabled()  // Region frames are fractions of the whole widget.
      .configurationDisplayName("Fibona")
      .description("What Fibona draws here.")
  }
}
