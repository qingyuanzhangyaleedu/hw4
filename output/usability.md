# Campus Customs usability improvements — Problem 9

Implemented and checked in the running shop on October 1, 2026. Open http://localhost:5173 with both servers running.

## 1. Catalogue filters — front end

Added category, maximum-price, and in-stock-size dropdowns beside the existing search and sort controls. Filters work together, stay in the page URL after refresh, and have a Clear filters button. Size filtering checks actual positive stock in SQLite.

This saves shoppers from opening items outside their budget or unavailable in their size. **Try it:** Products → Category: Hoodies → Maximum price: $70 or less → In-stock size: M. The supplied database returned 19 matching cards. Choosing $30 showed a clear empty state; Clear filters restored all 102 products.

## 2. Suggested chat questions — front end

Added one-click questions above the chat input. On browsing pages, shoppers can choose Hoodies under $70 or T-shirts in M. On an item page, these change to Check size M, Check the price, and Describe this item. Clicking sends a real question through the existing chatbot, with the current product ID.

This helps shoppers discover what the assistant can do without composing a question. **Try it:** Open Let’s talk Yale on Products and click Hoodies under $70; it returned six real cards. Open Champion Reverse Weave Hoodie 1, reopen chat, and click Check size M. The live reply confirmed 20 available, matching the detail page.

## 3. More catalogue matches — agent/backend

Added `show_more_products`. It remembers the latest search's category, keywords, budget, and size within the current conversation, and returns up to six previously unseen items. It keeps the filters unchanged and reports when the matches run out. Failed requests do not advance the saved search position.

This exposes more of the shop's selection without repeating cards or making shoppers restate their needs. **Try it:** Ask “Show me hoodies for $70 or less.” Then click Show more matches below the six cards, or type “Show more matching products.” The live check returned six different hoodies within the same budget, with zero repeated IDs. These cards open the same detail page as regular shop cards. Search position is temporary: after logout, server restart, or session expiry, start a new search; saved account messages remain available separately.

## 4. Product comparisons — agent/backend

Added `compare_products`, which reads two exact products' current prices, descriptions, and size stock together. Python calculates the price difference. The assistant returns a short comparison and cards for both items; missing products and missing size records are not replaced with guesses.

This helps shoppers choose without switching repeatedly between product pages, and reduces the separate tool lookups needed for a comparison. **Try it:** Ask “Compare Champion Reverse Weave Hoodie 1 and Brooks Brothers Double Knit Full Zip Hoodie Yale in XL.” The live reply correctly reported Champion at $68 with XL out of stock, Brooks Brothers at $88 with 12 in XL, and a $20 difference. Both cards appeared and used the existing product-detail route.

**Regression checks:** All 51 backend tests, the frontend production build, and lint passed. Live checks covered guest chat, dynamic cards, clicking a chat card into the full detail view, seed-user login, restored earlier history, and the new comparison reappearing with its cards after reload. Logout removed the account transcript from the guest widget. No new dependencies, database columns, or backend code files were needed.
