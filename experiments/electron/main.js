// Fibona on the desktop: the native shell, the way main.m is on iOS.
// Runs core.js and app.js as scripts on the main process's global scope, and exposes one object, `native`, for what a
// script cannot reach by itself: this directory, the terminal and the keychain. With `require` global, everything else,
// Electron included, is built in JavaScript.

const { app, safeStorage } = require("electron");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

// One file per account in the app's data directory, encrypted with the OS keychain through safeStorage.
const secretFile = (account) => path.join(app.getPath("userData"), account + ".bin");

globalThis.require = require;
globalThis.native = {
  dir: __dirname,
  log: (text) => process.stdout.write(text),
  keychainGet(account) {
    try {
      return safeStorage.decryptString(fs.readFileSync(secretFile(account)));
    } catch {
      return null;  // Missing, or no longer readable with this keychain.
    }
  },
  // Stores value, or deletes the account for null. 0 on success, like SecItemAdd; 1 when there is no keychain.
  keychainSet(account, value) {
    if (value === null) {
      fs.rmSync(secretFile(account), { force: true });
      return 0;
    }
    if (!safeStorage.isEncryptionAvailable()) return 1;
    fs.writeFileSync(secretFile(account), safeStorage.encryptString(value));
    return 0;
  },
};

app.whenReady().then(() => {
  for (const name of ["core", "app"]) {
    const file = path.join(__dirname, name + ".js");
    vm.runInThisContext(fs.readFileSync(file, "utf8"), { filename: file });
  }
});
