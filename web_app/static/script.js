// HTML escaping for safe DOM insertion
function escapeHtml(str) {
    var d = document.createElement('div');
    d.appendChild(document.createTextNode(str));
    return d.innerHTML;
}

// CSRF protection — runs immediately to patch fetch/XHR before subpage scripts
(function() {
    var token = document.querySelector('meta[name="csrf-token"]')?.content;
    if (!token) return;

    function injectCsrfField(form) {
        if (!form.querySelector('input[name="csrf_token"]')) {
            var i = document.createElement('input');
            i.type = 'hidden'; i.name = 'csrf_token'; i.value = token;
            form.appendChild(i);
        }
    }

    function isNonGetForm(f) {
        return f.method && f.method.toLowerCase() !== 'get';
    }

    // Inject into existing forms once DOM is ready
    function injectExistingForms() {
        document.querySelectorAll('form').forEach(function(f) {
            if (isNonGetForm(f)) injectCsrfField(f);
        });
        // Observe dynamically added forms
        new MutationObserver(function(mutations) {
            mutations.forEach(function(m) {
                m.addedNodes.forEach(function(node) {
                    if (node.nodeType !== 1) return;
                    var forms = node.tagName === 'FORM' ? [node] : node.querySelectorAll('form[method="post"], form[method="POST"]');
                    forms.forEach(injectCsrfField);
                });
            });
        }).observe(document.body, { childList: true, subtree: true });
    }

    if (document.body) {
        injectExistingForms();
    } else {
        document.addEventListener('DOMContentLoaded', injectExistingForms);
    }

    // Intercept fetch
    var _fetch = window.fetch;
    window.fetch = function(url, opts) {
        opts = opts || {};
        var m = (opts.method || 'GET').toUpperCase();
        if (m !== 'GET' && m !== 'HEAD') {
            if (opts.headers instanceof Headers) {
                if (!opts.headers.has('X-CSRFToken')) opts.headers.set('X-CSRFToken', token);
            } else {
                opts.headers = Object.assign({'X-CSRFToken': token}, opts.headers || {});
            }
        }
        return _fetch.call(this, url, opts);
    };

    // Intercept XMLHttpRequest
    var _xhrOpen = XMLHttpRequest.prototype.open;
    var _xhrSend = XMLHttpRequest.prototype.send;
    XMLHttpRequest.prototype.open = function(method) {
        this._csrfMethod = method;
        return _xhrOpen.apply(this, arguments);
    };
    XMLHttpRequest.prototype.send = function() {
        var m = (this._csrfMethod || 'GET').toUpperCase();
        if (m !== 'GET' && m !== 'HEAD') {
            this.setRequestHeader('X-CSRFToken', token);
        }
        return _xhrSend.apply(this, arguments);
    };
})();
