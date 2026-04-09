document.addEventListener('DOMContentLoaded', function() {
    const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content;
    if (!csrfToken) return;

    // Inject hidden CSRF field into all POST forms
    document.querySelectorAll('form[method="post"], form[method="POST"]').forEach(function(form) {
        if (!form.querySelector('input[name="csrf_token"]')) {
            const input = document.createElement('input');
            input.type = 'hidden';
            input.name = 'csrf_token';
            input.value = csrfToken;
            form.appendChild(input);
        }
    });

    // Also observe dynamically added forms
    new MutationObserver(function(mutations) {
        mutations.forEach(function(m) {
            m.addedNodes.forEach(function(node) {
                if (node.nodeType !== 1) return;
                var forms = node.tagName === 'FORM' ? [node] : node.querySelectorAll ? node.querySelectorAll('form[method="post"], form[method="POST"]') : [];
                forms.forEach(function(form) {
                    if (!form.querySelector('input[name="csrf_token"]')) {
                        const input = document.createElement('input');
                        input.type = 'hidden';
                        input.name = 'csrf_token';
                        input.value = csrfToken;
                        form.appendChild(input);
                    }
                });
            });
        });
    }).observe(document.body, { childList: true, subtree: true });

    // Add CSRF header to fetch requests
    const originalFetch = window.fetch;
    window.fetch = function(url, options) {
        options = options || {};
        if (options.method && options.method.toUpperCase() !== 'GET') {
            options.headers = options.headers || {};
            if (options.headers instanceof Headers) {
                if (!options.headers.has('X-CSRFToken')) options.headers.set('X-CSRFToken', csrfToken);
            } else {
                if (!options.headers['X-CSRFToken']) options.headers['X-CSRFToken'] = csrfToken;
            }
        }
        return originalFetch.call(this, url, options);
    };
});
