// Grab elements
const dropzone = document.getElementById("uploadDropzone");
const fileInput = document.getElementById("uploadInput");

const modalBackdrop = document.getElementById("uploadModalBackdrop");
const modalCloseBtn = document.getElementById("modalCloseBtn");
const modalCancelBtn = document.getElementById("modalCancelBtn");
const modalSubmitBtn = document.getElementById("modalSubmitBtn");

// Simple function to open modal
function openModal() {
  if (!modalBackdrop) return;
  modalBackdrop.classList.add("show");
}

// Simple function to close modal
function closeModal() {
  if (!modalBackdrop) return;
  modalBackdrop.classList.remove("show");
}

// When clicking dropzone, trigger hidden file input
if (dropzone && fileInput) {
  dropzone.addEventListener("click", () => {
    fileInput.click();
  });

  fileInput.addEventListener("change", (event) => {
    const file = event.target.files && event.target.files[0];
    if (!file) return;

    // TODO: Call Azure OCR here later.
    // For now, we can just pre-fill some dummy values or leave blank.
    // Example placeholder:
    document.getElementById("firstNameInput").value = "";
    document.getElementById("lastNameInput").value = "";
    document.getElementById("jobTitleInput").value = "";
    document.getElementById("officeEmailInput").value = "";
    document.getElementById("privateEmailInput").value = "";
    document.getElementById("officeNameInput").value = "";
    document.getElementById("phoneNumberInput").value = "";
    document.getElementById("industryInput").value = "";
    document.getElementById("companyLogoInput").value = "";

    openModal();
  });
}

// Modal buttons
if (modalCloseBtn) {
  modalCloseBtn.addEventListener("click", closeModal);
}
if (modalCancelBtn) {
  modalCancelBtn.addEventListener("click", closeModal);
}

if (modalSubmitBtn) {
  modalSubmitBtn.addEventListener("click", () => {
    // In real version, send data to backend / Azure / DB.
    alert("Details submitted (placeholder).");
    closeModal();
  });
}
