// ===== NAVIGATION BEHAVIOUR (HOME PAGE) =====
const homeBtn = document.querySelector(".nav-home");
const companyBtn = document.querySelector(".nav-company");
const uploadBtn = document.querySelector(".nav-upload");
const chatBtn = document.querySelector(".nav-chat");

if (homeBtn) {
  homeBtn.addEventListener("click", () => {
    window.location.href = "index.html";
  });
}

if (companyBtn) {
  companyBtn.addEventListener("click", () => {
    window.location.href = "info.html";
  });
}

if (uploadBtn) {
  uploadBtn.addEventListener("click", () => {
    window.location.href = "upload.html";
  });
}

if (chatBtn) {
  chatBtn.addEventListener("click", () => {
    window.location.href = "chat.html";
  });
}
