(() => {
  const btn = document.querySelector('.menu-toggle');
  const mobile = document.querySelector('#mobileMenu');
  if (btn && mobile) btn.addEventListener('click', () => {
    const expanded = btn.getAttribute('aria-expanded') === 'true';
    btn.setAttribute('aria-expanded', String(!expanded));
    mobile.hidden = expanded;
  });
  const input = document.querySelector('#filterInput');
  const cards = [...document.querySelectorAll('#storyGrid .story-card')];
  if (input && cards.length) input.addEventListener('input', () => {
    const query = input.value.trim().toLocaleLowerCase();
    let shown = 0;
    cards.forEach(card => {
      const match = card.textContent.toLocaleLowerCase().includes(query);
      card.hidden = !match;
      if (match) shown++;
    });
    const empty = document.querySelector('#filterEmpty');
    if (empty) empty.hidden = shown > 0;
  });

  const share = document.querySelector('.article-share');
  if (share) {
    const url = new URL(window.location.href);
    url.search = '';
    url.hash = '';
    const title = document.querySelector('.article-title h1').textContent.trim();
    const articleUrl = url.href;
    const text = `${title} ${articleUrl}`;
    const status = share.querySelector('.share-status');
    const copyInput = share.querySelector('.share-copy');
    const links = {
      whatsapp: `https://wa.me/?text=${encodeURIComponent(text)}`,
      x: `https://twitter.com/intent/tweet?url=${encodeURIComponent(articleUrl)}&text=${encodeURIComponent(title)}`,
      viber: `viber://forward?text=${encodeURIComponent(text)}`,
      telegram: `https://t.me/share/url?url=${encodeURIComponent(articleUrl)}&text=${encodeURIComponent(title)}`,
    };
    share.querySelectorAll('[data-share]').forEach(button => {
      button.addEventListener('click', async () => {
        const platform = button.dataset.share;
        if (!/^https?:$/.test(window.location.protocol)) {
          status.textContent = 'Open this article on the hosted website to share its public link.';
          return;
        }
        status.textContent = '';
        copyInput.hidden = true;
        if (platform === 'messenger') {
          const appId = document.querySelector('meta[name="facebook-app-id"]')?.content;
          if (appId) {
            const dialog = new URL('https://www.facebook.com/dialog/send');
            dialog.search = new URLSearchParams({
              app_id: appId,
              link: articleUrl,
              redirect_uri: articleUrl,
            }).toString();
            window.open(dialog.href, '_blank', 'noopener,noreferrer');
            return;
          }
          // Open during the click event so clipboard permissions do not trigger popup blocking.
          window.open('https://www.messenger.com/', '_blank', 'noopener,noreferrer');
          try {
            await navigator.clipboard.writeText(articleUrl);
            status.textContent = 'Article link copied. Paste it into your Messenger conversation to share.';
          } catch {
            copyInput.value = articleUrl;
            copyInput.hidden = false;
            copyInput.focus();
            copyInput.select();
            status.textContent = 'Copy this article link and paste it into your Messenger conversation.';
          }
          return;
        }
        if (platform === 'viber') {
          status.textContent = 'Opening Viber. The Viber app must be installed.';
          window.location.href = links.viber;
          return;
        }
        window.open(links[platform], '_blank', 'noopener,noreferrer');
      });
    });
  }

})();
