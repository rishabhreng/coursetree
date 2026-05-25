import re
from fastapi import Depends, HTTPException
import sqlite3 as sql
# Assuming other necessary imports/constants are present

@app.get("/api/courses/", response_model=CoursesResponse)
def search_courses(
    q: str,
    term_code: str = DEFAULT_COURSE_TERM_CODE,
    top_n_results: int = 50,
    offset: int = 0,
    db: sql.Connection = Depends(get_db),
) -> CoursesResponse:
    try:
        q = _clean_query(q)
        fts_query = _convert_to_fts_query(q)
        if not fts_query:
            return {}

        # 1. Prepare base WHERE clause and parameters
        where_clause = "WHERE global_search MATCH ?"
        base_params = [fts_query]

        if term_code != "all":
            where_clause += " AND term = ?"
            base_params.append(f"courses_{term_code}")

        # 2. Determine secondary sort and specific CTE parameters
        if re.match(r"^[A-Z]{4}\s*\d{3}$", q):
            # Exact course searches: recency tier -> search relevance
            secondary_sort = "bm25(global_search) ASC"
            cte_params = []
            
        elif q in VALID_SUBJECTS:
            # Subject-only searches: recency tier -> true numerical order
            secondary_sort = "CAST(SUBSTR(crs, ?) AS INTEGER) ASC"
            cte_params = [len(q) + 2]
            
        else:
            # General keyword searches: recency tier -> search relevance
            secondary_sort = "bm25(global_search) ASC"
            cte_params = []

        # 3. Build the universal CTE query
        sql_query = f"""
            WITH CourseStats AS (
                SELECT *,
                       -- Find the newest term for THIS course
                       MAX(CAST(REPLACE(term, 'courses_', '') AS INTEGER)) OVER (PARTITION BY crs) as course_max_term,
                       -- Find the absolute newest term across ALL matched courses
                       MAX(CAST(REPLACE(term, 'courses_', '') AS INTEGER)) OVER () as global_max_term
                FROM global_search
                {where_clause}
            ),
            RankedCourses AS (
                SELECT *,
                       DENSE_RANK() OVER (
                           ORDER BY 
                               -- Primary Sort: Rolling 2-year tiers
                               ((global_max_term - course_max_term) / 200) ASC, 
                               -- Secondary Sort: Injected based on search type
                               {secondary_sort}
                       ) as course_rank
                FROM CourseStats
            )
            -- 4. Select and paginate based on the unique course rank
            SELECT * FROM RankedCourses
            WHERE course_rank > ? AND course_rank <= ?
            ORDER BY course_rank ASC, term DESC
        """

        # Parameter binding order strictly follows the '?' placeholders in the query
        params = base_params + cte_params + [offset, offset + top_n_results]

        # 5. Execute and return
        rows = db.cursor().execute(sql_query, tuple(params)).fetchall()
        return _group_courses(rows)

    except sql.Error as e:
        raise HTTPException(status_code=500, detail=f"Database query failed: {str(e)}")
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Unexpected server error: {str(e)}"
        )