// Shared "who am I signed in as" indicator — a small circle with the first
// letter of the signed-in email, full email on hover, matching the pattern
// of a browser's own account avatar. Reads sessionStorage directly rather
// than each page's own getOperatorEmail()/getEmail() helper so this one file
// works unmodified on every page regardless of what that page calls its own
// accessor, or whether it defines one at all.
(function () {
  function currentEmail() {
    try {
      return sessionStorage.getItem("vyavastha_email") || "";
    } catch (e) {
      return "";
    }
  }

  function initAccountAvatar() {
    const el = document.getElementById("account-avatar");
    if (!el) return;
    const email = currentEmail().trim();
    if (!email) {
      // A nav-link avatar (attendee header) carries its own signed-out
      // fallback content (e.g. a generic person icon) and must stay visible
      // and clickable either way; an operator-page avatar has none and just
      // hides when nobody's signed in.
      if (el.dataset.avatarFallback) {
        el.textContent = el.dataset.avatarFallback;
        el.title = "";
      } else {
        el.classList.add("hidden");
      }
      return;
    }
    el.classList.remove("hidden");
    el.textContent = email[0].toUpperCase();
    el.title = email;
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initAccountAvatar);
  } else {
    initAccountAvatar();
  }
  window.initAccountAvatar = initAccountAvatar;
})();
