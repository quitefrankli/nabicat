document.addEventListener("DOMContentLoaded", function() {
    const openParam = new URLSearchParams(window.location.search).get("open");
    if (openParam) {
        history.replaceState(null, "", window.location.pathname);
    }

    const treeProjects = document.querySelectorAll(".tree-project");

    treeProjects.forEach(function(project) {
        const treeItem = project.closest(".tree-item");
        const posts = treeItem.querySelector(".tree-posts");

        const shouldOpen = project.classList.contains("active")
            || posts.querySelector(".tree-post.active")
            || project.dataset.project === openParam;

        if (shouldOpen) {
            treeItem.classList.add("expanded");
            posts.classList.add("show");
        }

        project.addEventListener("click", function(e) {
            e.preventDefault();
            treeItem.classList.toggle("expanded");
            posts.classList.toggle("show");
        });
    });

    const galleryForm = document.getElementById("gallery-meta-form");
    const progressWrap = document.querySelector("[data-gallery-upload-progress]");
    if (galleryForm && progressWrap) {
        const progressBar = progressWrap.querySelector("[data-gallery-upload-bar]");
        const progressTrack = progressWrap.querySelector(".hammock-upload-progress-track");
        const progressStatus = progressWrap.querySelector("[data-gallery-upload-status]");
        const submitButton = document.querySelector('[type="submit"][form="gallery-meta-form"]');
        const setProgress = value => {
            progressBar.style.width = value + "%";
            progressTrack.setAttribute("aria-valuenow", String(value));
        };

        galleryForm.addEventListener("submit", function(e) {
            e.preventDefault();
            progressWrap.hidden = false;
            setProgress(0);
            progressStatus.textContent = "Uploading...";
            if (submitButton) {
                submitButton.disabled = true;
                submitButton.dataset.originalText = submitButton.textContent;
                submitButton.textContent = "Uploading...";
            }

            const xhr = new XMLHttpRequest();
            xhr.open("POST", galleryForm.action);
            xhr.setRequestHeader("X-Requested-With", "XMLHttpRequest");
            xhr.upload.addEventListener("progress", function(event) {
                if (!event.lengthComputable) return;
                setProgress(Math.round((event.loaded / event.total) * 100));
                if (event.loaded === event.total) {
                    progressStatus.textContent = "Processing media...";
                    if (submitButton) submitButton.textContent = "Processing...";
                }
            });
            xhr.addEventListener("load", function() {
                let data = {};
                try {
                    data = JSON.parse(xhr.responseText);
                } catch (err) {
                    data = {};
                }
                if (xhr.status >= 200 && xhr.status < 300 && data.redirect_url) {
                    setProgress(100);
                    progressStatus.textContent = "Saved.";
                    window.location.assign(data.redirect_url);
                    return;
                }
                progressStatus.textContent = data.error || "Upload failed.";
                if (submitButton) {
                    submitButton.disabled = false;
                    submitButton.textContent = submitButton.dataset.originalText || "Update post";
                }
            });
            xhr.addEventListener("error", function() {
                progressStatus.textContent = "Upload failed.";
                if (submitButton) {
                    submitButton.disabled = false;
                    submitButton.textContent = submitButton.dataset.originalText || "Update post";
                }
            });
            xhr.send(new FormData(galleryForm));
        });
    }

    // Gallery lightbox
    const galleryButtons = document.querySelectorAll(".hammock-gallery-photo-btn");
    if (galleryButtons.length > 0) {
        const lb = document.createElement("div");
        lb.className = "hammock-lightbox";
        lb.innerHTML = '<span class="hammock-lightbox-close" aria-label="Close">&times;</span><img alt="">';
        document.body.appendChild(lb);
        const lbImg = lb.querySelector("img");
        const close = () => { lb.classList.remove("open"); lbImg.src = ""; };

        galleryButtons.forEach(btn => {
            btn.addEventListener("click", () => {
                lbImg.src = btn.dataset.full;
                lb.classList.add("open");
            });
        });
        lb.querySelector(".hammock-lightbox-close").addEventListener("click", close);
        lb.addEventListener("click", e => { if (e.target === lb) close(); });
        document.addEventListener("keydown", e => { if (e.key === "Escape") close(); });
    }

    const templateRadios = document.querySelectorAll('input[name="template"]');
    const templateFields = document.querySelectorAll("[data-template-field]");
    const syncTemplateFields = () => {
        const selected = document.querySelector('input[name="template"]:checked');
        if (!selected) return;
        templateFields.forEach(field => {
            field.hidden = field.dataset.templateField !== selected.value;
        });
    };
    templateRadios.forEach(radio => radio.addEventListener("change", syncTemplateFields));
    syncTemplateFields();

    const existingProject = document.getElementById("project-existing");
    const newProject = document.getElementById("project-new");
    if (existingProject && newProject) {
        existingProject.addEventListener("change", () => {
            if (existingProject.value) newProject.value = "";
        });
        newProject.addEventListener("input", () => {
            if (newProject.value) existingProject.value = "";
        });
    }
});
