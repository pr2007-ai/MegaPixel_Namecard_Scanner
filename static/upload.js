// =========================
// Upload page JS (Flask + Azure SQL submit)
// =========================

// Grab elements
const dropzone = document.getElementById("uploadDropzone");
const fileInput = document.getElementById("uploadInput");

const modalBackdrop = document.getElementById("uploadModalBackdrop");
const modalCloseBtn = document.getElementById("modalCloseBtn");
const modalCancelBtn = document.getElementById("modalCancelBtn");
const modalSubmitBtn = document.getElementById("modalSubmitBtn");

// Form inputs
const firstNameInput = document.getElementById("firstNameInput");
const lastNameInput = document.getElementById("lastNameInput");
const jobTitleInput = document.getElementById("jobTitleInput");
const officeEmailInput = document.getElementById("officeEmailInput");
const privateEmailInput = document.getElementById("privateEmailInput");
const officeNameInput = document.getElementById("officeNameInput");
const phoneNumberInput = document.getElementById("phoneNumberInput");
const industryInput = document.getElementById("industryInput");
const companyLogoInput = document.getElementById("companyLogoInput");

// Store the latest selected file (optional for later OCR upload)
let selectedFile = null;

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

// Reset fields (optional helper)
function clearFields() {
  if (firstNameInput) firstNameInput.value = "";
  if (lastNameInput) lastNameInput.value = "";
  if (jobTitleInput) jobTitleInput.value = "";
  if (officeEmailInput) officeEmailInput.value = "";
  if (privateEmailInput) privateEmailInput.value = "";
  if (officeNameInput) officeNameInput.value = "";
  if (phoneNumberInput) phoneNumberInput.value = "";
  if (industryInput) industryInput.value = "";
  if (companyLogoInput) companyLogoInput.value = "";
}

// When clicking dropzone, trigger hidden file input
if (dropzone && fileInput) {
  dropzone.addEventListener("click", () => fileInput.click());

  fileInput.addEventListener("change", (event) => {
    const file = event.target.files && event.target.files[0];
    if (!file) return;

    selectedFile = file;

    // For now: just clear fields. Later you can call OCR and autofill.
    clearFields();

    openModal();
  });
}

// Modal buttons
if (modalCloseBtn) modalCloseBtn.addEventListener("click", closeModal);
if (modalCancelBtn) modalCancelBtn.addEventListener("click", closeModal);

// Submit → send form values to Flask → Flask inserts into Azure SQL
if (modalSubmitBtn) {
  modalSubmitBtn.addEventListener("click", async () => {
    // Disable button to prevent double submit
    modalSubmitBtn.disabled = true;
    modalSubmitBtn.textContent = "Submitting...";

    const payload = {
      firstName: firstNameInput ? firstNameInput.value.trim() : "",
      lastName: lastNameInput ? lastNameInput.value.trim() : "",
      jobTitle: jobTitleInput ? jobTitleInput.value.trim() : "",
      officeEmail: officeEmailInput ? officeEmailInput.value.trim() : "",
      privateEmail: privateEmailInput ? privateEmailInput.value.trim() : "",
      officeName: officeNameInput ? officeNameInput.value.trim() : "",
      phoneNumber: phoneNumberInput ? phoneNumberInput.value.trim() : "",
      industry: industryInput ? industryInput.value.trim() : "",
      companyLogo: companyLogoInput ? companyLogoInput.value.trim() : ""
    };

    try {
      const res = await fetch("/submit-contact", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      });

      // If backend crashed or returned HTML, this prevents .json() from throwing silently
      const contentType = res.headers.get("content-type") || "";
      let out = null;

      if (contentType.includes("application/json")) {
        out = await res.json();
      } else {
        const text = await res.text();
        throw new Error("Server did not return JSON. Response:\n" + text);
      }

      if (!res.ok || !out.ok) {
        throw new Error(out?.error || "Unknown error");
      }

      alert("✅ Saved to database!");
      closeModal();

      // Optional: clear file input + selected file after submit
      selectedFile = null;
      if (fileInput) fileInput.value = "";

    } catch (err) {
      alert("❌ Failed to save: " + err.message);
      console.error(err);
    } finally {
      // Re-enable button
      modalSubmitBtn.disabled = false;
      modalSubmitBtn.textContent = "Submit";
    }
  });
}
