const dropZone = document.getElementById("dropZone");
const fileInput = document.getElementById("fileInput");
const uploadBtn = document.getElementById("uploadBtn");
const uploadSection = document.getElementById("uploadSection");
const loadingSection = document.getElementById("loadingSection");
const loadingText = document.getElementById("loadingText");
const resultSection = document.getElementById("resultSection");
const errorSection = document.getElementById("errorSection");
const originalImage = document.getElementById("originalImage");
const resultImage = document.getElementById("resultImage");
const resultImageWrapper = document.getElementById("resultImageWrapper");
const resultCardLabel = document.getElementById("resultCardLabel");
const resultSubtitle = document.getElementById("resultSubtitle");
const downloadBtn = document.getElementById("downloadBtn");
const tryAnotherBtn = document.getElementById("tryAnotherBtn");
const tryAgainBtn = document.getElementById("tryAgainBtn");
const errorText = document.getElementById("errorText");
const shadowToggleLabel = document.getElementById("shadowToggleLabel");
const shadowToggle = document.getElementById("shadowToggle");

let isProcessing = false;

// ── Shadow toggle ────────────────────────────────────────
shadowToggleLabel.addEventListener("click", (e) => {
    // Prevent any click bubbling issues; toggle manually
    e.preventDefault();
    shadowToggle.checked = !shadowToggle.checked;
    shadowToggleLabel.classList.toggle("is-checked", shadowToggle.checked);
});

// ── Section management ───────────────────────────────────
function showSection(section) {
    [uploadSection, loadingSection, resultSection, errorSection].forEach(s => {
        s.classList.add("hidden");
    });
    section.classList.remove("hidden");
}

function showError(message) {
    errorText.textContent = message;
    showSection(errorSection);
}

function resetToUpload() {
    isProcessing = false;

    // Clear image sources BEFORE clearing handlers to avoid stale callbacks
    resultImage.onload = null;
    resultImage.onerror = null;
    originalImage.src = "";
    resultImage.src = "";
    downloadBtn.href = "#";

    // Reset result card label
    resultCardLabel.innerHTML = "Result";
    resultSubtitle.textContent = "Here's your result. Download it as a transparent PNG.";

    // Reset file input so the same file can be re-selected
    fileInput.value = "";
    try { fileInput.value = null; } catch (e) {}

    showSection(uploadSection);
}

function clearResultImage() {
    resultImage.onload = null;
    resultImage.onerror = null;
    resultImage.src = "";
}

// ── Core upload + processing ─────────────────────────────
async function processFile(file) {
    if (!file || isProcessing) return;

    const allowedTypes = ["image/jpeg", "image/png", "image/webp"];
    if (!allowedTypes.includes(file.type)) {
        showError("Invalid file type. Please upload a JPG, PNG, or WEBP image.");
        return;
    }

    if (file.size > 10 * 1024 * 1024) {
        showError("File too large. Maximum size is 10MB.");
        return;
    }

    isProcessing = true;
    clearResultImage();

    const withShadow = shadowToggle.checked;
    const previewUrl = URL.createObjectURL(file);
    originalImage.src = previewUrl;

    // Update loading text to reflect shadow mode
    loadingText.textContent = withShadow
        ? "Removing background & adding shadow..."
        : "Removing background...";

    showSection(loadingSection);

    const formData = new FormData();
    formData.append("image", file);
    formData.append("shadow", withShadow ? "1" : "0");

    // Reset file input now so the same file can be re-uploaded later
    fileInput.value = "";
    try { fileInput.value = null; } catch (e) {}

    try {
        const response = await fetch("/remove-bg", {
            method: "POST",
            body: formData,
            cache: "no-store",
            headers: { "X-Requested-With": "XMLHttpRequest" },
        });

        const data = await response.json();

        if (!response.ok || data.error) {
            throw new Error(data.error || "Something went wrong. Please try again.");
        }

        const resultId = data.result_id;
        const hasShadow = data.shadow === true;
        const previewSrc = `/preview/${resultId}?t=${Date.now()}`;

        // Update result label and subtitle based on shadow mode
        if (hasShadow) {
            resultCardLabel.innerHTML = 'Result <span class="shadow-badge">✦ Shadow</span>';
            resultSubtitle.textContent = "Background removed with a professional drop shadow applied.";
        } else {
            resultCardLabel.innerHTML = "Result";
            resultSubtitle.textContent = "Here's your result. Download it as a transparent PNG.";
        }

        // Set handlers BEFORE src to avoid race conditions
        resultImage.onload = () => {
            isProcessing = false;
            showSection(resultSection);
        };
        resultImage.onerror = () => {
            isProcessing = false;
            showError("Failed to load the result image. Please try again.");
        };

        downloadBtn.href = `/download/${resultId}`;
        resultImage.src = previewSrc;

    } catch (err) {
        isProcessing = false;
        showError(err.message || "Something went wrong. Please try again.");
    }
}

// ── Event listeners ──────────────────────────────────────

uploadBtn.addEventListener("click", (e) => {
    e.preventDefault();
    e.stopPropagation();
    if (!isProcessing) fileInput.click();
});

fileInput.addEventListener("change", (e) => {
    const file = e.target.files[0];
    if (file) processFile(file);
});

dropZone.addEventListener("dragover", (e) => {
    e.preventDefault();
    if (!isProcessing) dropZone.classList.add("dragover");
});

dropZone.addEventListener("dragleave", (e) => {
    if (!dropZone.contains(e.relatedTarget)) {
        dropZone.classList.remove("dragover");
    }
});

dropZone.addEventListener("drop", (e) => {
    e.preventDefault();
    dropZone.classList.remove("dragover");
    if (!isProcessing) {
        const file = e.dataTransfer.files[0];
        if (file) processFile(file);
    }
});

// Click anywhere on the drop zone that isn't the button or the file input
dropZone.addEventListener("click", (e) => {
    if (e.target === fileInput || e.target === uploadBtn || uploadBtn.contains(e.target)) return;
    if (!isProcessing) fileInput.click();
});

tryAnotherBtn.addEventListener("click", resetToUpload);
tryAgainBtn.addEventListener("click", resetToUpload);
