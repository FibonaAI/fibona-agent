// The agent's app in a view: runs the program index.html sends (chrome.storage.local fibona_app, or app/bootstrap.js
// until there is one) as the body of an async function, at global scope, with the fibona_ names below.
// Errors go to the console and the session's fibona_app_log, where the agent can see them.
(() => {
  let view;  // The FibonaChannel to index.html.
  const outer = new FibonaNativeBridge((message) => view.send({ type: "native", message }));
  const dataCallbacks = new Set();
  const report = (text, level = "error") => view?.request("log", { text, level }).catch(() => {});
  const chromeCall = (path, ...args) => view.request("chrome", { path, args });

  Object.assign(globalThis, {
    fibona_ui_context: {},  // {id, surface, url} of this view.
    fibona_send: (data) => view.request("send", { data }),
    fibona_on_data: (callback) => {
      dataCallbacks.add(callback);
      return () => dataCallbacks.delete(callback);
    },
    fibona_show_console: (visible = true) => view.request("show-console", { visible }),
    fibona_chrome: Object.assign(chromeCall, {
      callback: (path, ...args) => view.request("chrome", { path, args, callback: true }),
    }),
    fibona_view: outer.root(),
  });
  addEventListener("error", (event) => report(`App error: ${event.error?.stack ?? event.message}`));
  addEventListener("unhandledrejection", (event) => report(`App error: ${event.reason?.stack ?? event.reason}`));
  for (const level of ["log", "info", "warn", "error"]) {
    const original = console[level].bind(console);
    console[level] = (...values) => {
      original(...values);
      report(values.map(value => {
        if (typeof value === "string") return value;
        try { return JSON.stringify(value) ?? String(value); } catch { return String(value); }
      }).join(" "), level);
    };
  }

  async function run({ code, context }) {
    fibona_ui_context = context;
    try {
      await (0, eval)(`(async () => {\n${code}\n})()`);  // Indirect eval: global scope, as for cells.
    } catch (error) {
      report(`App error: ${error?.stack ?? error}`);
    }
  }

  addEventListener("message", (event) => {
    if (event.source !== parent || event.data?.type !== "fibona_connect" || view) return;
    const port = event.ports[0];
    view = new FibonaChannel((message) => port.postMessage(message), {
      message: async (message) => {
        if (message.type === "run") await run(message);
        else if (message.type === "context") fibona_ui_context = message.context;
        else if (message.type === "native") await outer.receive(message.message);
        else if (message.type === "data") {
          for (const callback of dataCallbacks) {
            try {
              await callback(message.data);
            } catch (error) {
              report(`App error in fibona_on_data: ${error?.stack ?? error}`);
            }
          }
        }
      },
    });
    port.onmessage = ({ data }) => view.receive(data);
    view.send({ type: "connected" });
  });
  parent.postMessage({ type: "fibona_ready" }, "*");
})();
