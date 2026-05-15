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
});
