(function () {
  const debugPrefix = '[debug] page-content';
  const normalize = (value) => (value || '').trim().replace(/\s+/g, ' ');
  const snippet = () => {
    const content = document.getElementById('page-content');
    if (!content) {
      return '<missing page-content>'; 
    }
    const text = normalize(content.textContent);
    return text.length > 120 ? `${text.slice(0, 120)}...` : text;
  };

  const log = (...args) => {
    // eslint-disable-next-line no-console
    console.log(debugPrefix, ...args, 'snippet=', snippet());
  };

  const watch = () => {
    const target = document.getElementById('page-content');
    if (!target) {
      return;
    }

    const observer = new MutationObserver((records) => {
      const added = records.reduce((sum, record) => sum + record.addedNodes.length, 0);
      const removed = records.reduce((sum, record) => sum + record.removedNodes.length, 0);
      log('mutation', `added=${added}`, `removed=${removed}`);
    });
    observer.observe(target, { childList: true, subtree: true });

    target.addEventListener(
      'click',
      (event) => {
        const targetElement = event.target;
        const label = normalize(targetElement.textContent);
        const attrs = [
          targetElement.id ? `id=${targetElement.id}` : null,
          targetElement.className ? `class=${targetElement.className}` : null,
          targetElement.href ? `href=${targetElement.href}` : null,
        ]
          .filter(Boolean)
          .join(' ');
        log(
          'click',
          targetElement.tagName,
          label ? `label=${label.slice(0, 40)}` : 'label=<none>',
          attrs,
        );
      },
      true,
    );
  };

  document.addEventListener('DOMContentLoaded', watch);
  if (document.readyState === 'complete' || document.readyState === 'interactive') {
    watch();
  }
})();
