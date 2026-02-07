document.addEventListener('DOMContentLoaded', () => {
    const swipeContainer = document.getElementById('swipe-container');
    if (!swipeContainer) return;
    
    const rejectBtn = document.getElementById('reject-btn');
    const saveBtn = document.getElementById('save-btn');
    const applyBtn = document.getElementById('apply-job-btn');
    const emptyStack = document.getElementById('empty-stack');
    const jobCounter = document.getElementById('job-counter');
    
    let jobCards = Array.from(swipeContainer.querySelectorAll('.job-card'));
    let activeCard = null;
    let startX = 0, currentX = 0, isDragging = false;

    function updateCounter() {
        if (jobCounter) jobCounter.textContent = `${jobCards.length} remaining`;
    }

    function initCards() {
        jobCards = Array.from(swipeContainer.querySelectorAll('.job-card:not(.swiped-left):not(.swiped-right)'));
        jobCards.forEach((card, i) => {
            card.style.zIndex = jobCards.length - i;
            card.style.transform = i === 0 ? 'translateY(0) scale(1)' : 
                                   i === 1 ? 'translateY(8px) scale(0.96)' : 
                                   i === 2 ? 'translateY(16px) scale(0.92)' : 'translateY(24px) scale(0.88)';
            card.style.opacity = i > 2 ? '0' : (i === 0 ? '1' : i === 1 ? '0.7' : '0.5');
            card.style.pointerEvents = i === 0 ? 'auto' : 'none';
        });
        activeCard = jobCards[0] || null;
        updateCounter();
        
        if (jobCards.length === 0 && emptyStack) {
            swipeContainer.classList.add('d-none');
            if (rejectBtn) rejectBtn.parentElement.classList.add('d-none');
            emptyStack.classList.remove('d-none');
        }
    }

    function getJobData(card) {
        return {
            title: card.querySelector('.job-title')?.textContent || '',
            company: card.querySelector('.job-company')?.textContent?.replace(/\s+/g, ' ').trim() || '',
            location: card.querySelector('.job-location')?.textContent?.trim() || '',
            url: card.querySelector('.job-card-footer a')?.href || ''
        };
    }

    async function recordAction(card, action) {
        const jobId = card.dataset.jobId;
        try {
            await fetch(`/jswipe/api/job/${jobId}/${action}`, {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(getJobData(card))
            });
        } catch (e) {
            console.error('Failed to record action:', e);
        }
    }

    function removeCard(card, direction) {
        const actionMap = {left: 'reject', right: 'save', apply: 'apply'};
        recordAction(card, actionMap[direction]);
        card.classList.add(`swiped-${direction === 'apply' ? 'right' : direction}`);
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
        activeCard.classList.remove('swiping-left', 'swiping-right');
    }

    function handleDragMove(e) {
        if (!isDragging || !activeCard) return;
        e.preventDefault();
        currentX = (e.pageX || e.touches[0].pageX) - startX;
        activeCard.style.transform = `translateX(${currentX}px) rotate(${currentX * 0.05}deg)`;
        activeCard.classList.toggle('swiping-left', currentX < 0);
        activeCard.classList.toggle('swiping-right', currentX > 0);
    }

    function handleDragEnd() {
        if (!isDragging || !activeCard) return;
        isDragging = false;
        activeCard.style.cursor = 'grab';
        activeCard.classList.remove('swiping-left', 'swiping-right');

        if (currentX > 100) removeCard(activeCard, 'right');
        else if (currentX < -100) removeCard(activeCard, 'left');
        else {
            activeCard.style.transition = 'transform 0.3s ease';
            activeCard.style.transform = 'translateY(0) scale(1)';
        }
        currentX = 0;
    }

    swipeContainer.addEventListener('mousedown', handleDragStart);
    swipeContainer.addEventListener('mousemove', handleDragMove);
    document.addEventListener('mouseup', handleDragEnd);
    swipeContainer.addEventListener('touchstart', handleDragStart, {passive: false});
    swipeContainer.addEventListener('touchmove', handleDragMove, {passive: false});
    document.addEventListener('touchend', handleDragEnd);

    if (rejectBtn) rejectBtn.addEventListener('click', () => activeCard && removeCard(activeCard, 'left'));
    if (saveBtn) saveBtn.addEventListener('click', () => activeCard && removeCard(activeCard, 'right'));
    if (applyBtn) applyBtn.addEventListener('click', () => activeCard && removeCard(activeCard, 'apply'));
    document.addEventListener('keydown', (e) => {
        if (e.key === 'ArrowLeft' && activeCard) removeCard(activeCard, 'left');
        else if (e.key === 'ArrowRight' && activeCard) removeCard(activeCard, 'right');
    });

    initCards();
});
