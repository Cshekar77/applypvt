/* ═══════════════════════════════════════════════════════════════
   BCA Admissions Portal — main.js
   Fixed: NaN guard, file validation, size check, dark mode,
          mobile nav, flash dismiss, IST helpers
   ═══════════════════════════════════════════════════════════════ */

'use strict';

/* ════════════════════════════════════
   DARK MODE — persisted via localStorage
════════════════════════════════════ */
(function initDarkMode() {
  const saved = localStorage.getItem('bca_dark_mode');
  if (saved === 'true') {
    document.documentElement.classList.add('dark-pending');
  }
})();

document.addEventListener('DOMContentLoaded', function () {

  /* Apply dark class on body after DOM ready */
  const saved = localStorage.getItem('bca_dark_mode');
  if (saved === 'true') {
    document.body.classList.add('dark');
    document.documentElement.classList.remove('dark-pending');
    updateDarkIcons(true);
  }

  /* ── Dark mode toggle button (if present in page) ── */
  const darkToggle = document.getElementById('darkModeToggle');
  if (darkToggle) {
    darkToggle.addEventListener('click', toggleDarkMode);
  }

  /* ── Auto-dismiss flash messages ── */
  initFlashDismiss();

  /* ── Mobile nav close on link click ── */
  initMobileNavLinks();

  /* ── Button ripple effect ── */
  initButtonRipple();

  /* ── Animate elements on scroll ── */
  initScrollReveal();

  /* ── Restore form state indicators ── */
  initFormState();

});

/* ────────────────────────────────────
   DARK MODE
──────────────────────────────────── */
function toggleDarkMode() {
  const isDark = document.body.classList.toggle('dark');
  localStorage.setItem('bca_dark_mode', isDark ? 'true' : 'false');
  updateDarkIcons(isDark);

  /* Smooth transition flash */
  document.body.style.transition = 'background .35s, color .35s';
  setTimeout(function () { document.body.style.transition = ''; }, 400);
}

function updateDarkIcons(isDark) {
  const icons = document.querySelectorAll('.dark-mode-icon');
  icons.forEach(function (icon) {
    icon.className = isDark
      ? 'fas fa-sun dark-mode-icon'
      : 'fas fa-moon dark-mode-icon';
  });
  const toggles = document.querySelectorAll('#darkModeToggle');
  toggles.forEach(function (btn) {
    btn.setAttribute('title', isDark ? 'Switch to Light Mode' : 'Switch to Dark Mode');
    btn.setAttribute('aria-label', isDark ? 'Switch to Light Mode' : 'Switch to Dark Mode');
  });
}

/* ────────────────────────────────────
   MOBILE NAV
──────────────────────────────────── */
function toggleMobileNav() {
  const drawer = document.getElementById('mobileDrawer');
  const icon   = document.getElementById('hamburgerIcon');
  if (!drawer) return;
  const isOpen = drawer.classList.toggle('open');
  if (icon) icon.className = isOpen ? 'fas fa-times' : 'fas fa-bars';
  /* Prevent body scroll when drawer open */
  document.body.style.overflow = isOpen ? 'hidden' : '';
}

function initMobileNavLinks() {
  const drawer = document.getElementById('mobileDrawer');
  if (!drawer) return;
  drawer.querySelectorAll('.nav-link').forEach(function (link) {
    link.addEventListener('click', function () {
      drawer.classList.remove('open');
      const icon = document.getElementById('hamburgerIcon');
      if (icon) icon.className = 'fas fa-bars';
      document.body.style.overflow = '';
    });
  });

  /* Close drawer when clicking outside */
  document.addEventListener('click', function (e) {
    const hamburger = document.getElementById('navHamburger');
    if (!drawer || !hamburger) return;
    if (!drawer.contains(e.target) && !hamburger.contains(e.target)) {
      drawer.classList.remove('open');
      const icon = document.getElementById('hamburgerIcon');
      if (icon) icon.className = 'fas fa-bars';
      document.body.style.overflow = '';
    }
  });

  /* Close on Escape */
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') {
      drawer.classList.remove('open');
      const icon = document.getElementById('hamburgerIcon');
      if (icon) icon.className = 'fas fa-bars';
      document.body.style.overflow = '';
    }
  });
}

/* ────────────────────────────────────
   FLASH MESSAGES — auto dismiss
──────────────────────────────────── */
function initFlashDismiss() {
  /* Handle both static and dynamically injected flashes */
  function dismissFlashes(container) {
    container = container || document;
    container.querySelectorAll('.flash').forEach(function (el) {
      if (el.dataset.autoDismiss) return; /* already scheduled */
      el.dataset.autoDismiss = '1';

      /* Add close button */
      const closeBtn = document.createElement('button');
      closeBtn.innerHTML = '&times;';
      closeBtn.style.cssText = [
        'background:none', 'border:none', 'cursor:pointer',
        'font-size:1.1rem', 'color:inherit', 'opacity:.6',
        'margin-left:auto', 'padding:0 .25rem',
        'line-height:1', 'flex-shrink:0'
      ].join(';');
      closeBtn.setAttribute('aria-label', 'Dismiss');
      closeBtn.addEventListener('click', function () { fadeRemove(el); });
      el.appendChild(closeBtn);

      /* Auto dismiss after 4.5s */
      setTimeout(function () { fadeRemove(el); }, 4500);
    });
  }

  function fadeRemove(el) {
    if (!el || !el.parentNode) return;
    el.style.transition = 'opacity .4s, transform .4s, max-height .4s, margin .4s, padding .4s';
    el.style.opacity    = '0';
    el.style.transform  = 'translateY(-6px) scale(.97)';
    el.style.maxHeight  = el.offsetHeight + 'px';
    requestAnimationFrame(function () {
      el.style.maxHeight  = '0';
      el.style.margin     = '0';
      el.style.padding    = '0';
      el.style.overflow   = 'hidden';
    });
    setTimeout(function () { if (el.parentNode) el.remove(); }, 450);
  }

  dismissFlashes();

  /* Watch for dynamically added flashes */
  const flashWrap = document.querySelector('.flash-wrap');
  if (flashWrap && window.MutationObserver) {
    const observer = new MutationObserver(function (mutations) {
      mutations.forEach(function (m) {
        m.addedNodes.forEach(function (node) {
          if (node.nodeType === 1) dismissFlashes(node.parentNode || document);
        });
      });
    });
    observer.observe(flashWrap, { childList: true, subtree: true });
  }
}

/* ────────────────────────────────────
   OTHER BOARD TOGGLE
──────────────────────────────────── */
function toggleOther(select, divId) {
  const div = document.getElementById(divId);
  if (!div) return;
  const show = select.value === 'Other';
  div.style.display = show ? 'block' : 'none';

  /* Animate in */
  if (show) {
    div.style.opacity   = '0';
    div.style.transform = 'translateY(-6px)';
    div.style.transition = 'opacity .25s, transform .25s';
    requestAnimationFrame(function () {
      div.style.opacity   = '1';
      div.style.transform = 'translateY(0)';
    });
    const input = div.querySelector('input');
    if (input) {
      input.required = true;
      setTimeout(function () { input.focus(); }, 50);
    }
  } else {
    const input = div.querySelector('input');
    if (input) { input.required = false; input.value = ''; }
  }
}

/* ────────────────────────────────────
   PERCENTAGE CALCULATOR — BUG FIXED
──────────────────────────────────── */
function calcPct(tId, oId, pId) {
  const totalEl    = document.getElementById(tId);
  const obtainedEl = document.getElementById(oId);
  const displayEl  = document.getElementById(pId);
  if (!displayEl) return;

  const total    = parseFloat(totalEl ? totalEl.value : '');
  const obtained = parseFloat(obtainedEl ? obtainedEl.value : '');

  /* BUG FIX: Guard NaN and invalid ranges */
  const totalValid    = !isNaN(total)    && isFinite(total)    && total > 0;
  const obtainedValid = !isNaN(obtained) && isFinite(obtained) && obtained >= 0;
  const rangeValid    = obtainedValid && totalValid && obtained <= total;

  if (totalValid && obtainedValid && rangeValid) {
    const pct = (obtained / total * 100).toFixed(2);
    displayEl.textContent = pct + '%';
    displayEl.classList.add('has-value');
    displayEl.style.transition = 'all .3s';

    /* Colour coding */
    if (parseFloat(pct) >= 75) {
      displayEl.style.color = '#1e7e52';
    } else if (parseFloat(pct) >= 50) {
      displayEl.style.color = '#c9a84c';
    } else {
      displayEl.style.color = '#c0392b';
    }

  } else if (totalValid && obtainedValid && !rangeValid) {
    /* BUG FIX: obtained > total — show error */
    displayEl.textContent = '⚠ Obtained > Total';
    displayEl.classList.add('has-value');
    displayEl.style.color = '#c0392b';
  } else {
    displayEl.textContent = '—';
    displayEl.classList.remove('has-value');
    displayEl.style.color = '';
  }
}

/* ────────────────────────────────────
   FILE UPLOAD — BUG FIXED
──────────────────────────────────── */
const ALLOWED_TYPES  = ['application/pdf'];
const MAX_FILE_BYTES = 5 * 1024 * 1024; /* 5 MB */

function handleUpload(input, areaId, fnId) {
  const area = document.getElementById(areaId);
  const fn   = document.getElementById(fnId);
  if (!area || !fn) return;

  if (!input.files || !input.files[0]) return;

  const file = input.files[0];

  /* BUG FIX: File type validation */
  const isPdf = ALLOWED_TYPES.includes(file.type) ||
                file.name.toLowerCase().endsWith('.pdf');
  if (!isPdf) {
    showUploadError(area, fn, '⚠ Only PDF files are accepted.');
    input.value = ''; /* clear selection */
    return;
  }

  /* BUG FIX: File size validation */
  if (file.size > MAX_FILE_BYTES) {
    const sizeMB = (file.size / 1024 / 1024).toFixed(1);
    showUploadError(area, fn, '⚠ File too large (' + sizeMB + ' MB). Max 5 MB.');
    input.value = '';
    return;
  }

  /* Success state */
  const shortName = file.name.length > 40
    ? file.name.substring(0, 37) + '…'
    : file.name;

  fn.textContent   = '✓ ' + shortName;
  fn.style.display = 'block';
  fn.style.color   = '#1e7e52';
  area.classList.remove('upload-error');
  area.classList.add('has-file');

  const p = area.querySelector('.upload-text-wrap p');
  if (p) p.textContent = 'File selected';

  /* Animate tick */
  const icon = area.querySelector('.upload-icon-wrap');
  if (icon) {
    icon.style.transition = 'transform .3s cubic-bezier(.34,1.56,.64,1)';
    icon.style.transform  = 'scale(1.15) rotate(-8deg)';
    setTimeout(function () { icon.style.transform = ''; }, 350);
  }
}

function showUploadError(area, fn, msg) {
  fn.textContent   = msg;
  fn.style.display = 'block';
  fn.style.color   = '#c0392b';
  area.classList.remove('has-file');
  area.classList.add('upload-error');

  /* Shake animation */
  area.style.transition = 'transform .08s';
  const shakes = ['-4px', '4px', '-3px', '3px', '0px'];
  let i = 0;
  const shake = setInterval(function () {
    area.style.transform = 'translateX(' + shakes[i] + ')';
    i++;
    if (i >= shakes.length) {
      clearInterval(shake);
      area.style.transform = '';
    }
  }, 60);
}

/* ────────────────────────────────────
   BUTTON RIPPLE EFFECT
──────────────────────────────────── */
function initButtonRipple() {
  document.querySelectorAll('.btn').forEach(function (btn) {
    btn.addEventListener('mouseenter', function (e) {
      const rect = btn.getBoundingClientRect();
      const x = ((e.clientX - rect.left) / rect.width  * 100).toFixed(1) + '%';
      const y = ((e.clientY - rect.top)  / rect.height * 100).toFixed(1) + '%';
      btn.style.setProperty('--x', x);
      btn.style.setProperty('--y', y);
    });
  });
}

/* ────────────────────────────────────
   SCROLL REVEAL
──────────────────────────────────── */
function initScrollReveal() {
  if (!window.IntersectionObserver) return;

  const targets = document.querySelectorAll(
    '.form-card, .view-card, .verify-card, .stat-card, ' +
    '.admin-nav-card, .info-card, .student-row, .sum-box, .sum-card'
  );

  targets.forEach(function (el, i) {
    el.style.opacity   = '0';
    el.style.transform = 'translateY(18px)';
    el.style.transition = 'opacity .45s ease, transform .45s cubic-bezier(.22,.68,0,1.2)';
    el.style.transitionDelay = (i % 6 * 0.06) + 's'; /* max 6 stagger per group */
  });

  const observer = new IntersectionObserver(function (entries) {
    entries.forEach(function (entry) {
      if (entry.isIntersecting) {
        entry.target.style.opacity   = '1';
        entry.target.style.transform = 'translateY(0)';
        observer.unobserve(entry.target);
      }
    });
  }, { threshold: 0.1, rootMargin: '0px 0px -30px 0px' });

  targets.forEach(function (el) { observer.observe(el); });
}

/* ────────────────────────────────────
   FORM STATE — restore upload indicators
──────────────────────────────────── */
function initFormState() {
  /* If page reloads with a file already chosen (browser cache), show it */
  document.querySelectorAll('input[type="file"]').forEach(function (input) {
    if (input.files && input.files[0]) {
      const areaId = input.closest('[id]') ? input.closest('[id]').id : null;
      const fnEl   = input.parentElement
        ? input.parentElement.querySelector('.upload-filename')
        : null;
      if (fnEl && input.files[0]) {
        fnEl.textContent   = '✓ ' + input.files[0].name;
        fnEl.style.display = 'block';
        const area = input.closest('.upload-area');
        if (area) area.classList.add('has-file');
      }
    }
  });
}

/* ────────────────────────────────────
   IST DATE FORMATTER (client-side)
──────────────────────────────────── */
/**
 * Convert a UTC ISO string or Date to IST (UTC+5:30)
 * Returns: "dd/mm/yyyy, h:mm AM/PM IST"
 */
function toIST(input) {
  if (!input) return '—';
  const d = (input instanceof Date) ? input : new Date(input);
  if (isNaN(d)) return String(input);
  const ist  = new Date(d.getTime() + (5 * 60 + 30) * 60 * 1000);
  const dd   = String(ist.getUTCDate()).padStart(2, '0');
  const mm   = String(ist.getUTCMonth() + 1).padStart(2, '0');
  const yyyy = ist.getUTCFullYear();
  let   hr   = ist.getUTCHours();
  const min  = String(ist.getUTCMinutes()).padStart(2, '0');
  const ampm = hr >= 12 ? 'PM' : 'AM';
  hr = hr % 12 || 12;
  return dd + '/' + mm + '/' + yyyy + ', ' + hr + ':' + min + ' ' + ampm + ' IST';
}

/**
 * Format a date string or Date to dd/mm/yyyy (no time)
 */
function formatDOB(input) {
  if (!input) return '—';
  if (typeof input === 'string') {
    /* Handle YYYY-MM-DD */
    if (/^\d{4}-\d{2}-\d{2}/.test(input)) {
      const parts = input.substring(0, 10).split('-');
      return parts[2] + '/' + parts[1] + '/' + parts[0];
    }
    return input;
  }
  const d = input instanceof Date ? input : new Date(input);
  if (isNaN(d)) return String(input);
  return String(d.getDate()).padStart(2, '0') + '/' +
         String(d.getMonth() + 1).padStart(2, '0') + '/' +
         d.getFullYear();
}

/* Auto-format any element with data-dob attribute */
document.addEventListener('DOMContentLoaded', function () {
  document.querySelectorAll('[data-dob]').forEach(function (el) {
    el.textContent = formatDOB(el.dataset.dob || el.textContent.trim());
  });
  document.querySelectorAll('[data-ist]').forEach(function (el) {
    el.textContent = toIST(el.dataset.ist || el.textContent.trim());
  });
});

/* ────────────────────────────────────
   CONFIRM DIALOGS — styled
──────────────────────────────────── */
function confirmAction(msg, form) {
  /* Falls back to native confirm if no custom modal */
  if (window.confirm(msg)) {
    if (form) form.submit();
    return true;
  }
  return false;
}

/* ────────────────────────────────────
   COPY TO CLIPBOARD
──────────────────────────────────── */
function copyToClipboard(text, btn) {
  if (!navigator.clipboard) {
    /* Fallback */
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.style.position = 'fixed'; ta.style.opacity = '0';
    document.body.appendChild(ta);
    ta.select();
    document.execCommand('copy');
    document.body.removeChild(ta);
    showCopySuccess(btn);
    return;
  }
  navigator.clipboard.writeText(text).then(function () {
    showCopySuccess(btn);
  });
}

function showCopySuccess(btn) {
  if (!btn) return;
  const original = btn.innerHTML;
  btn.innerHTML = '<i class="fas fa-check"></i> Copied!';
  btn.style.background = '#d1fae5';
  btn.style.color      = '#065f46';
  setTimeout(function () {
    btn.innerHTML = original;
    btn.style.background = '';
    btn.style.color      = '';
  }, 2000);
}

/* ────────────────────────────────────
   SEARCH FILTER UTILITY
──────────────────────────────────── */
/**
 * Generic live search across a list of rows
 * @param {string} inputId   - ID of the search input
 * @param {string} rowSel    - CSS selector for filterable rows
 * @param {string[]} attrs   - data-* attribute names to search in
 */
function initLiveSearch(inputId, rowSel, attrs) {
  const input = document.getElementById(inputId);
  if (!input) return;
  input.addEventListener('input', function () {
    const q = input.value.trim().toLowerCase();
    document.querySelectorAll(rowSel).forEach(function (row) {
      if (!q) { row.style.display = ''; return; }
      const text = attrs
        .map(function (a) { return (row.dataset[a] || row.textContent || '').toLowerCase(); })
        .join(' ');
      row.style.display = text.includes(q) ? '' : 'none';
    });
  });
}

/* ────────────────────────────────────
   EXPOSE GLOBALS
──────────────────────────────────── */
window.toggleMobileNav  = toggleMobileNav;
window.toggleDarkMode   = toggleDarkMode;
window.toggleOther      = toggleOther;
window.calcPct          = calcPct;
window.handleUpload     = handleUpload;
window.toIST            = toIST;
window.formatDOB        = formatDOB;
window.confirmAction    = confirmAction;
window.copyToClipboard  = copyToClipboard;
window.initLiveSearch   = initLiveSearch;