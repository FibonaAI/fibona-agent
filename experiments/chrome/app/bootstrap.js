// The default App is editable content. The outer page owns an ordinary, editable #view-switch button.
document.documentElement.style.cssText = "color-scheme:light;background:#faf8f4;color:#242321;font:16px/1.5 -apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif";
document.body.style.cssText = "margin:0;min-height:100dvh;box-sizing:border-box;padding:48px max(24px,calc((100% - 840px)/2))";
const style = document.createElement("style");
style.textContent =
  "h1{display:flex;align-items:center;gap:12px;font-size:24px;line-height:1.25;font-weight:600;letter-spacing:-.02em;margin:0 0 16px}" +
  "p{max-width:38ch;margin:0;color:#65635e;line-height:1.75}" +
  "@media(max-width:640px){body{padding-top:64px!important}}";
document.head.append(style);
const heading = document.createElement("h1");
heading.textContent = "Fibona";
const logo = document.createElement("img");
logo.src = "../icons/fibona.svg";
logo.width = logo.height = 32;
logo.alt = "";
heading.prepend(logo);
const description = document.createElement("p");
description.textContent = "Your app starts here. Open the Console and ask Fibona to build something.";
document.body.append(heading, description);
