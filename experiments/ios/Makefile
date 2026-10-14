SDK := $(shell xcrun --sdk iphonesimulator --show-sdk-path)
APP = build/Fibona.app
SIM ?= Fibona
# Signing teams have no default: pass TEAM=<your development team> (and TF_TEAM= for TestFlight).
TEAM ?=
# TestFlight: a paid team, and the App Store Connect app's bundle identifier.
TF_TEAM ?=
TF_BUNDLE ?= ai.fibona.ios.test
DEVICE_BUNDLE ?= ai.fibona.ios.$(TEAM)
# The first paired iPhone connected by cable; pass DEVICE=<identifier from `xcrun devicectl list devices`> to choose.
DEVICE ?= $(shell xcrun devicectl list devices --hide-headers --hide-default-columns --columns identifier \
  --filter "connectionProperties.transportType == 'wired'" 2>/dev/null | grep -v "No devices" | head -1)

.DEFAULT_GOAL := build
.PHONY: build run device testflight

# The app icon, from public/favicon.svg: full-bleed on its own background (iOS rounds the corners), without alpha.
ICON = Assets.xcassets/AppIcon.appiconset/icon.png
$(ICON): public/favicon.svg
	mkdir -p build
	rsvg-convert -w 1024 -h 1024 --background-color '#FAF8F4' $< -o build/icon.png
	sips -s format jpeg build/icon.png --out build/icon.jpg >/dev/null && sips -s format png build/icon.jpg --out $@ >/dev/null

# The floating button's image, from the same SVG: 48 points at 3x.
HANDLE = Assets.xcassets/Handle.imageset/handle.png
$(HANDLE): public/favicon.svg
	rsvg-convert -w 144 -h 144 --background-color '#FAF8F4' $< -o $@

build: $(ICON) $(HANDLE)  # The app for the simulator. The model settings are asked for at first launch.
	mkdir -p $(APP)
	xcrun --sdk iphonesimulator clang -fobjc-arc -target arm64-apple-ios17.0-simulator -isysroot $(SDK) \
	  -framework UIKit -framework JavaScriptCore -framework Security -o $(APP)/Fibona main.m
	sed 's/$$(PRODUCT_BUNDLE_IDENTIFIER)/ai.fibona.ios/g; s/$$(CURRENT_PROJECT_VERSION)/1/g' Info.plist > $(APP)/Info.plist
	cp core.js app.js $(APP)/
	xcrun actool Assets.xcassets --compile $(APP) --platform iphonesimulator --minimum-deployment-target 17.0 \
	  --target-device iphone --app-icon AppIcon --output-partial-info-plist build/assets.plist >/dev/null
	/usr/libexec/PlistBuddy -c "Merge build/assets.plist" $(APP)/Info.plist >/dev/null
	codesign -f -s - $(APP)  # No entitlements: the simulator refuses ad-hoc builds with them; `make device` signs them.

run: build  # Type the task in the app. Output appears here too.
	xcrun simctl list devices available | grep -q " $(SIM) (" || xcrun simctl create $(SIM) "iPhone 16 Pro"
	xcrun simctl boot $(SIM) 2>/dev/null || true
	open -a Simulator
	xcrun simctl install $(SIM) $(APP)
	xcrun simctl launch --console-pty --terminate-running-process $(SIM) ai.fibona.ios

# The app on a cabled iPhone, signed by Xcode for TEAM; the first run registers the phone, the App ID and the profile.
# Output appears here. With a free personal team, the profile lasts 7 days: run this again after it expires.
Fibona.xcodeproj/project.pbxproj: project.pbxproj
	mkdir -p Fibona.xcodeproj
	cp $< $@

device: Fibona.xcodeproj/project.pbxproj $(ICON) $(HANDLE)
	test -n "$(TEAM)" || { echo "Pass TEAM=<your development team>"; exit 1; }
	test -n "$(DEVICE)" || { echo "No iPhone connected by cable: unlock it, trust this Mac, or pass DEVICE=..."; exit 1; }
	xcodebuild -project Fibona.xcodeproj -scheme Fibona -configuration Debug -quiet \
	  -destination id=$$(xcrun devicectl device info details --device $(DEVICE) | awk '/ udid:/ {print $$NF}') \
	  -allowProvisioningUpdates -allowProvisioningDeviceRegistration \
	  DEVELOPMENT_TEAM=$(TEAM) PRODUCT_BUNDLE_IDENTIFIER=$(DEVICE_BUNDLE) SYMROOT=$(CURDIR)/build/device
	xcrun devicectl device install app --device $(DEVICE) build/device/Debug-iphoneos/Fibona.app
	xcrun devicectl device process launch --console --terminate-existing \
	  --device $(DEVICE) $(DEVICE_BUNDLE)

# Archive for TF_TEAM and upload to App Store Connect, where the app must already exist with bundle TF_BUNDLE.
# Signing is automatic with this Mac's Xcode account; the build number is the time, so each upload is newer.
testflight: Fibona.xcodeproj/project.pbxproj $(ICON) $(HANDLE)
	test -n "$(TF_TEAM)" || { echo "Pass TF_TEAM=<your paid development team>"; exit 1; }
	xcodebuild -project Fibona.xcodeproj -scheme Fibona -configuration Release -destination generic/platform=iOS \
	  -archivePath build/Fibona.xcarchive -quiet -allowProvisioningUpdates archive \
	  DEVELOPMENT_TEAM=$(TF_TEAM) PRODUCT_BUNDLE_IDENTIFIER=$(TF_BUNDLE) CURRENT_PROJECT_VERSION=$$(date +%Y%m%d%H%M)
	printf '%s\n' '<?xml version="1.0" encoding="UTF-8"?>' '<plist version="1.0"><dict>' \
	  '<key>method</key><string>app-store-connect</string><key>destination</key><string>upload</string>' \
	  '<key>teamID</key><string>$(TF_TEAM)</string><key>signingStyle</key><string>automatic</string>' \
	  '</dict></plist>' > build/ExportOptions.plist
	xcodebuild -exportArchive -archivePath build/Fibona.xcarchive -exportOptionsPlist build/ExportOptions.plist \
	  -exportPath build/export -allowProvisioningUpdates
