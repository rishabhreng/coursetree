type CourseSection = {
    term: string;
    score?: number;
    crn: string;
    crs: string;
    title: string;
    instructors: string;
    meeting_times: string;
    credits: string;
    course_page?: string | null;
};

type SearchResponse = Record<string, CourseSection[]>;

document.addEventListener('DOMContentLoaded', () => {
    const form = document.getElementById('searchForm') as HTMLFormElement;
    const result = document.getElementById('result') as HTMLDivElement;
    const searchAll = document.getElementById('searchAll') as HTMLButtonElement;

    async function doSearch(endpoint: 'search' | 'searchall') {
        const q = (document.getElementById('q') as HTMLInputElement).value.trim();
        const term = (document.getElementById('term') as HTMLInputElement).value.trim();
        const top = Number((document.getElementById('top') as HTMLInputElement).value);

        if (!q) return;

        const url = endpoint === 'search'
            ? `/search/?q=${encodeURIComponent(q)}&term_code=${encodeURIComponent(term)}&top_n_results=${top}`
            : `/searchall?q=${encodeURIComponent(q)}&top_n_results=${top}`;

        const response = await fetch(url);
        const data = (await response.json()) as SearchResponse;
        renderTree(data);
    }

    form.onsubmit = (event) => {
        event.preventDefault();
        doSearch('search');
    };

    searchAll.onclick = () => doSearch('searchall');

    function renderTree(data: SearchResponse) {
        result.innerHTML = '';
        Object.entries(data).forEach(([courseCode, courseSections]) => {
            const item = document.createElement('details');
            item.className = 'item';
            item.open = false;

            const summary = document.createElement('summary');
            summary.innerHTML = `<strong>${courseCode}</strong> <span class='badge'>${courseSections.length}</span>`;
            item.appendChild(summary);

            const list = document.createElement('div');
            list.className = 'section-list';
            courseSections.forEach(section => {
                const card = document.createElement('div');
                card.className = 'section-card';

                const h = document.createElement('h4');
                h.textContent = `${section.crs}: ${section.title}`;
                card.appendChild(h);

                const meta = document.createElement('p');
                meta.innerHTML = `<span class='pill'>${section.term}</span> <span class='pill'>${section.crn}</span> <span class='pill'>${section.credits} cr</span>`;
                card.appendChild(meta);

                const info = document.createElement('p');
                info.innerHTML = `<strong>Instructor:</strong> ${section.instructors || 'TBA'}<br><strong>When:</strong> ${section.meeting_times || 'TBA'}`;
                card.appendChild(info);

                if (section.course_page) {
                    const link = document.createElement('a');
                    link.href = section.course_page;
                    link.target = '_blank';
                    link.rel = 'noopener';
                    link.textContent = 'Course page';
                    link.className = 'link-btn';
                    card.appendChild(link);
                }

                list.appendChild(card);
            });

            item.appendChild(list);
            result.appendChild(item);
        });
    }

    // initial load
    doSearch('search');
});
