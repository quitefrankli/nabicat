document.addEventListener("DOMContentLoaded", function() {
    const treeProjects = document.querySelectorAll(".tree-project");

    treeProjects.forEach(function(project) {
        const treeItem = project.closest(".tree-item");
        const posts = treeItem.querySelector(".tree-posts");

        if (project.classList.contains("active") || posts.querySelector(".tree-post.active")) {
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
