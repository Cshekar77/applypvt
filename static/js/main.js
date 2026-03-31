// Toggle "Other board" text input
function toggleOther(select, divId) {
  const div = document.getElementById(divId);
  if (div) div.style.display = select.value === 'Other' ? 'block' : 'none';
}

// Auto-calculate percentage from total and obtained marks
function calcPct(tId, oId, pId) {
  const total    = parseFloat(document.getElementById(tId).value);
  const obtained = parseFloat(document.getElementById(oId).value);
  const el       = document.getElementById(pId);
  if (!el) return;
  if (total > 0 && obtained >= 0 && obtained <= total) {
    el.textContent = (obtained / total * 100).toFixed(2) + '%';
    el.classList.add('has-value');
  } else {
    el.textContent = '—';
    el.classList.remove('has-value');
  }
}

// Handle PDF upload display
function handleUpload(input, areaId, fnId) {
  const area = document.getElementById(areaId);
  const fn   = document.getElementById(fnId);
  if (!area || !fn) return;
  if (input.files && input.files[0]) {
    fn.textContent  = '✓ ' + input.files[0].name;
    fn.style.display = 'block';
    area.classList.add('has-file');
    const p = area.querySelector('.upload-text-wrap p');
    if (p) p.textContent = 'File selected';
  }
}

// Auto-dismiss flash messages after 4 seconds
document.addEventListener('DOMContentLoaded', function () {
  const flashes = document.querySelectorAll('.flash');
  flashes.forEach(function (el) {
    setTimeout(function () {
      el.style.transition = 'opacity .4s';
      el.style.opacity = '0';
      setTimeout(function () { el.remove(); }, 400);
    }, 4000);
  });
});
