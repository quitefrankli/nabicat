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
});
