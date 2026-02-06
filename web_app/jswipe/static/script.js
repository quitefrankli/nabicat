document.addEventListener('DOMContentLoaded', () => {
    const swipeContainer = document.getElementById('swipe-container');
    const rejectBtn = document.getElementById('reject-btn');
    const applyBtn = document.getElementById('apply-btn');
    const emptyStack = document.getElementById('empty-stack');
    const jobCounter = document.getElementById('job-counter');
    
    if (!swipeContainer) return;
    
    let jobCards = Array.from(swipeContainer.querySelectorAll('.job-card'));
    let activeCard = null;
    let startX = 0;
    let currentX = 0;
    let isDragging = false;

    function updateCounter() {
        if (jobCounter) {
            const remaining = jobCards.length;
            jobCounter.textContent = `${remaining} remaining`;
        }
    }

    function initCards() {
        jobCards = Array.from(swipeContainer.querySelectorAll('.job-card:not(.swiped-left):not(.swiped-right)'));
        
        jobCards.forEach((card, index) => {
            card.style.zIndex = jobCards.length - index;
            
            // Reset transforms for proper stacking
            if (index === 0) {
                card.style.transform = 'translateY(0) scale(1)';
                card.style.opacity = '1';
                card.style.pointerEvents = 'auto';
            } else if (index === 1) {
                card.style.transform = 'translateY(8px) scale(0.96)';
                card.style.opacity = '0.7';
                card.style.pointerEvents = 'none';
            } else if (index === 2) {
                card.style.transform = 'translateY(16px) scale(0.92)';
                card.style.opacity = '0.5';
                card.style.pointerEvents = 'none';
            } else {
                card.style.transform = 'translateY(24px) scale(0.88)';
                card.style.opacity = '0';
                card.style.pointerEvents = 'none';
            }
        });
        
        activeCard = jobCards[0] || null;
        updateCounter();
        
        // Show empty state if no cards left
        if (jobCards.length === 0 && emptyStack) {
            swipeContainer.classList.add('d-none');
            rejectBtn.parentElement.classList.add('d-none');
            emptyStack.classList.remove('d-none');
            if (jobCounter) jobCounter.textContent = '0 remaining';
        }
    }

    function removeCard(card, direction) {
        const jobId = card.dataset.jobId;
        console.log(`Swiped ${direction} on job ${jobId}`);
        
        // Add swipe class for animation
        card.classList.add(`swiped-${direction}`);
        
        // Remove from DOM after animation
        setTimeout(() => {
            card.style.display = 'none';
            initCards();
        }, 500);
    }

    function handleDragStart(e) {
        if (!activeCard) return;
        isDragging = true;
        startX = (e.pageX || e.touches[0].pageX);
        activeCard.style.transition = 'none';
        activeCard.style.cursor = 'grabbing';
        
        // Remove any existing swipe classes
        activeCard.classList.remove('swiping-left', 'swiping-right');
    }

    function handleDragMove(e) {
        if (!isDragging || !activeCard) return;
        e.preventDefault();
        
        currentX = (e.pageX || e.touches[0].pageX) - startX;
        const rotation = currentX * 0.05;
        const opacity = Math.min(Math.abs(currentX) / 100, 1);
        
        activeCard.style.transform = `translateX(${currentX}px) rotate(${rotation}deg)`;
        
        // Show swipe indicators
        if (currentX < 0) {
            activeCard.classList.add('swiping-left');
            activeCard.classList.remove('swiping-right');
        } else if (currentX > 0) {
            activeCard.classList.add('swiping-right');
            activeCard.classList.remove('swiping-left');
        }
    }

    function handleDragEnd() {
        if (!isDragging || !activeCard) return;
        isDragging = false;
        activeCard.style.cursor = 'grab';
        activeCard.classList.remove('swiping-left', 'swiping-right');

        const threshold = 100;
        
        if (currentX > threshold) {
            // Swiped right - save
            removeCard(activeCard, 'right');
        } else if (currentX < -threshold) {
            // Swiped left - skip
            removeCard(activeCard, 'left');
        } else {
            // Snap back
            activeCard.style.transition = 'transform 0.3s ease';
            activeCard.style.transform = 'translateY(0) scale(1)';
        }
        
        currentX = 0;
    }

    // Mouse events
    swipeContainer.addEventListener('mousedown', handleDragStart);
    swipeContainer.addEventListener('mousemove', handleDragMove);
    document.addEventListener('mouseup', handleDragEnd);

    // Touch events
    swipeContainer.addEventListener('touchstart', handleDragStart, { passive: false });
    swipeContainer.addEventListener('touchmove', handleDragMove, { passive: false });
    document.addEventListener('touchend', handleDragEnd);

    // Button controls
    if (rejectBtn) {
        rejectBtn.addEventListener('click', () => {
            if (activeCard) removeCard(activeCard, 'left');
        });
    }

    if (applyBtn) {
        applyBtn.addEventListener('click', () => {
            if (activeCard) removeCard(activeCard, 'right');
        });
    }

    // Keyboard controls
    document.addEventListener('keydown', (e) => {
        if (e.key === 'ArrowLeft' && activeCard) {
            removeCard(activeCard, 'left');
        } else if (e.key === 'ArrowRight' && activeCard) {
            removeCard(activeCard, 'right');
        }
    });

    // Initialize
    initCards();
});
