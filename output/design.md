# Storefront design — Problem 10

## Direction: the campus print studio

The shop now looks like a campus poster brought to life: ink black, warm paper, electric pink, oversized lettering, and a tilted **CC** stamp. The same treatment runs through Home, Products, About Us, account forms, product details, and chat.

## Type, color, and hierarchy

**Archivo Black** gives headings a bold poster voice; **DM Sans** keeps descriptions and forms easy to read. Headings scale with screen width, while product names and prices stay compact enough to scan. The palette uses black `#191918`, paper `#F4EFE5`, pink `#FF68B4`, and pale pink `#F8D7E7`. Black text on pink has a calculated contrast ratio of **6.61:1**. A faint dot texture, offset shadows, and the matching CC favicon make the brand recognizable without extra decorative images.

## Put the merch on display

Home pairs its headline with a large real hoodie photo, the actual product name and price, and a direct detail-page link. Shared product cards give photos generous space without cropping the garment. Prices get a pink underline; detail pages use a larger price label beside full descriptions and stock. Out-of-stock sizes say **“Out of stock”** and have dashed borders and crossed-out size labels, so shoppers do not have to interpret color alone.

## Motion and the merch desk

Buttons lift slightly, card photos gently enlarge on hover, and the chat panel opens in 200 milliseconds. Product loading has placeholder cards; the assistant shows animated dots and **“Finding your answer…”** while working. These cues distinguish a pending action from an unresponsive page. Reduced-motion preferences disable the animations and transitions.

The chat is now **“The merch desk.”** Its stamp-style launcher, black header, pink shopper messages, visible speaker labels, and matching product cards connect it to the shop. The existing account-history notice and quick-question buttons remain visible.

## Phone use and verification

Phone layouts stack the detail view and account form, keep products in two columns, and size chat to the available viewport. Inputs use 16-pixel text; keyboard focus has a black outline and pink ring. Google fonts have local fallback fonts.

**Verified October 4, 2026:** checked desktop, 820-pixel tablet, and 390-/320-pixel phone layouts; corrected a narrow-screen headline overflow. Live checks passed for filters, guest chat, a generated $98 bomber-jacket card, clicking through to its original detail view, creating an account, logging back in, and restoring its message and card after reload and a fresh login. Seed login restored its separate earlier history; logout cleared account messages from the guest panel. All **51 backend tests**, the frontend production build, and lint passed.

These choices should help shoppers recognize the store, inspect the clothes, and get answers with fewer distractions. This homework remains a browsing storefront; no checkout was added. The live checks left one demo account (`hw4-design-check-20261004@example.com`) and its two saved chat rows in the local database.
