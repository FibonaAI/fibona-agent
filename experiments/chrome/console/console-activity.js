/** Show agent steps and messages; source code stays available behind each step. */
export class ConsoleActivity {
  constructor(container) {
    this.container = container;
    this.records = new Map();
    this.localSequence = 0;
  }

  clear() {
    this.records.clear();
    this.container.replaceChildren();
  }

  appendLocal(text, type = 'output') {
    this.update({id:'local-'+ ++this.localSequence,type:type||'output',text});
  }

  update(item) {
    const follow = this.container.scrollHeight - this.container.scrollTop - this.container.clientHeight < 40;
    let node = this.records.get(item.id);
    if (!node) {
      node = document.createElement('article');
      this.records.set(item.id,node);
      this.container.append(node);
    }
    const expanded = node.querySelector('details')?.open;
    node.className = 'activity ' + (item.result?.error || item.level === 'error' ? 'error' : item.level === 'warn' ? 'warning' : item.type);
    node.dataset.kind = item.type;
    node.hidden = item.type === 'cell' && item.summary === 'Run the bootstrap cell.' && !item.result?.error;
    node.replaceChildren();
    if (item.type === 'cell') {
      const heading = document.createElement('div'), title = document.createElement('strong'), status = document.createElement('span');
      heading.className = 'step-heading';
      title.textContent = item.summary;
      status.className = 'step-status';
      status.textContent = item.status === 'running' ? 'In progress' : item.status === 'error' ? 'Failed' : 'Done';
      heading.append(title,status);
      const details = document.createElement('details'), summary = document.createElement('summary'), code = document.createElement('pre');
      details.open = expanded ?? false;
      summary.textContent = 'Code';
      code.className = 'cell-code';
      code.textContent = item.code;
      details.append(summary,code);
      node.append(heading);
      if (item.result?.error) {
        const error = document.createElement('p'), stack = document.createElement('pre');
        error.textContent = item.result.error.split('\n')[0];
        stack.textContent = item.result.error;
        node.append(error);
        details.append(stack);
      }
      node.append(details);
    } else {
      const label = document.createElement('span'), text = document.createElement('pre');
      label.className = 'message-label';
      label.textContent = item.type === 'user' ? 'You' : item.type === 'error' ? 'Error' : 'Fibona';
      text.textContent = item.text;
      node.append(label,text);
    }
    if (follow) this.container.scrollTop = this.container.scrollHeight;
  }
}
