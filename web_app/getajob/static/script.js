document.addEventListener('DOMContentLoaded', () => {
    const swipeContainer = document.getElementById('swipe-container');
    const rejectBtn = document.getElementById('reject-btn');
    const applyBtn = document.getElementById('apply-btn');
    let jobCards = Array.from(swipeContainer.querySelectorAll('.job-card'));

    let activeCard = null;
    let startX = 0;
    let currentX = 0;
    let isDragging = false;

    function initCards() {
        jobCards = Array.from(swipeContainer.querySelectorAll('.job-card'));
        jobCards.forEach((card, index) => {
            card.style.zIndex = jobCards.length - index;
            card.style.transform = `translateY(${index * 10}px)`;
            if (index !== 0) {
                card.style.display = 'block';
                card.style.opacity = '0.5';
            }
        });
        activeCard = jobCards[0] || null;
        if(activeCard) {
            activeCard.style.opacity = '1';
        }
    }

    function removeCard(card, direction) {
        const jobId = card.dataset.jobId;
        console.log(`Swiped ${direction} on job ${jobId}`);
        
        card.style.transition = 'transform 0.5s ease, opacity 0.5s ease';
        const rotation = (Math.random() - 0.5) * 30; // Random rotation
        const translateX = direction === 'right' ? '500px' : '-500px';
        card.style.transform = `translateX(${translateX}) rotate(${rotation}deg)`;
        card.style.opacity = '0';

        setTimeout(() => {
            card.remove();
            jobCards.shift();
            initCards();
        }, 500);
    }

    function handleDragStart(e) {
        if (!activeCard) return;
        isDragging = true;
        startX = e.pageX || e.touches[0].pageX;
        activeCard.style.transition = 'none';
        activeCard.style.cursor = 'grabbing';
    }

    function handleDragMove(e) {
        if (!isDragging || !activeCard) return;
        e.preventDefault();
        currentX = (e.pageX || e.touches[0].pageX) - startX;
        const rotation = currentX * 0.1;
        activeCard.style.transform = `translateX(${currentX}px) rotate(${rotation}deg)`;
    }

    function handleDragEnd() {
        if (!isDragging || !activeCard) return;
        isDragging = false;
        activeCard.style.cursor = 'grab';

        const threshold = 100;
        if (currentX > threshold) {
            removeCard(activeCard, 'right');
        } else if (currentX < -threshold) {
            removeCard(activeCard, 'left');
        } else {
            activeCard.style.transition = 'transform 0.3s ease';
            activeCard.style.transform = '';
        }
        currentX = 0;
    }

    swipeContainer.addEventListener('mousedown', handleDragStart);
    swipeContainer.addEventListener('mousemove', handleDragMove);
    document.addEventListener('mouseup', handleDragEnd);

    swipeContainer.addEventListener('touchstart', handleDragStart);
    swipeContainer.addEventListener('touchmove', handleDragMove);
    document.addEventListener('touchend', handleDragEnd);

    rejectBtn.addEventListener('click', () => {
        if (activeCard) removeCard(activeCard, 'left');
    });

    applyBtn.addEventListener('click', () => {
        if (activeCard) removeCard(activeCard, 'right');
    });

    initCards();
});

