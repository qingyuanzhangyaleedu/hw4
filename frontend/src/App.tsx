import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import {
  BrowserRouter,
  Link,
  NavLink,
  Route,
  Routes,
  useLocation,
  useParams,
  useSearchParams,
} from "react-router-dom";
import {
  ApiError,
  createAccount,
  getCurrentUser,
  getProduct,
  getProducts,
  login,
  logout,
} from "./api";
import type { ProductDetail, ProductSummary, User } from "./api";
import {
  Arrow,
  ErrorState,
  Loading,
  ProductCard,
  ProductImage,
} from "./components";
import { money } from "./format";
import ChatWidget from "./ChatWidget";

function useCatalogue(category = "", maxPrice = "", size = "") {
  const [result, setResult] = useState<{ key: string; products: ProductSummary[]; error: string }>({ key: "", products: [], error: "" });
  const [attempt, setAttempt] = useState(0);
  const key = JSON.stringify([category, maxPrice, size, attempt]);
  useEffect(() => {
    const controller = new AbortController();
    getProducts(controller.signal, { category, maxPrice, size })
      .then((products) => {
        if (!controller.signal.aborted) setResult({ key, products, error: "" });
      })
      .catch(() => {
        if (!controller.signal.aborted)
          setResult({ key, products: [], error: "We couldn’t load the collection. Please try again in a moment." });
      });
    return () => controller.abort();
  }, [category, maxPrice, size, key]);
  return {
    products: result.products,
    loading: result.key !== key,
    error: result.key === key ? result.error : "",
    retry: () => setAttempt((value) => value + 1),
  };
}

function RouteChange() {
  const { pathname } = useLocation();
  useEffect(() => {
    window.scrollTo(0, 0);
    const page =
      pathname === "/"
        ? "Home"
        : pathname === "/products"
          ? "Products"
          : pathname === "/about"
            ? "About Us"
            : pathname === "/login"
              ? "Log in"
              : pathname === "/signup"
                ? "Create account"
                : "Product details";
    document.title = `${page} | Campus Customs`;
    document.getElementById("main-content")?.focus({ preventScroll: true });
  }, [pathname]);
  return null;
}

function Header({
  user,
  loading,
  onLogout,
}: {
  user: User | null;
  loading: boolean;
  onLogout: () => Promise<void>;
}) {
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  async function signOut() {
    setPending(true);
    setError("");
    try {
      await onLogout();
    } catch {
      setError("We couldn’t log you out. Please try again.");
    } finally {
      setPending(false);
    }
  }
  return (
    <>
      <a className="skip-link" href="#main-content">
        Skip to content
      </a>
      <div className="announcement">
        <span>FOR THE CAMPUS. FOR THE CULTURE.</span>
        <span>
          VISIT US AT 57 BROADWAY <Arrow />
        </span>
      </div>
      <header className="site-header">
        <div className="header-inner">
          <Link className="wordmark" to="/" aria-label="Campus Customs home">
            <span className="brand-seal" aria-hidden="true">
              CC
            </span>
            <span>
              <strong>Campus Customs</strong>
              <small>NEW HAVEN, CT · YALE BULLDOG BLUE</small>
            </span>
          </Link>
          <nav aria-label="Main navigation">
            <NavLink to="/" end>
              Home
            </NavLink>
            <NavLink to="/products">Products</NavLink>
            <NavLink to="/about">About Us</NavLink>
            <span className="nav-divider" aria-hidden="true" />
            {loading ? (
              <span className="auth-greeting" role="status">
                Checking account…
              </span>
            ) : user ? (
              <>
                <span className="auth-greeting" title={user.name}>
                  Hi, {user.first_name || user.name}
                </span>
                <button
                  className="nav-logout"
                  onClick={signOut}
                  disabled={pending}
                >
                  {pending ? "Logging out…" : "Log out"}
                </button>
              </>
            ) : (
              <>
                <NavLink to="/login">Log in</NavLink>
                <NavLink to="/signup" className="nav-signup">
                  Create account <Arrow />
                </NavLink>
              </>
            )}
          </nav>
        </div>
        {error && (
          <p className="auth-banner-error" role="alert">
            {error}
          </p>
        )}
      </header>
    </>
  );
}

function Home() {
  const { products, loading, error, retry } = useCatalogue();
  const hero =
    products.find(
      (product) => product.id === "champion-reverse-weave-hoodie-1",
    ) ?? products[0];
  const featuredIds = [
    "2025-yale-vs-harvard-t-shirt",
    "basic-hoodie-big-yale",
    "baseball-left-chest-crewneck",
    "champion-reverse-weave-hoodie-1",
  ];
  const preferred = featuredIds.flatMap(
    (id) => products.find((product) => product.id === id) ?? [],
  );
  const featured = [
    ...preferred,
    ...products.filter((product) => !featuredIds.includes(product.id)),
  ].slice(0, 4);
  return (
    <>
      <section className="hero">
        <div className="hero-copy">
          <p className="eyebrow">
            <span className="small-line" /> NEW HAVEN ROOTS. YALE SPIRIT.
          </p>
          <h1>
            Campus.
            <br />
            Culture.
            <br />
            <em>Customs.</em>
          </h1>
          <p className="hero-intro">
            For the walk to class, the roar from the stands, and every Bulldog
            moment in between. Find a little Yale to take with you.
          </p>
          <Link className="button" to="/products">
            Find your Yale favorite <Arrow />
          </Link>
          <div className="hero-note">
            <span className="mini-y" aria-hidden="true">
              Y
            </span>
            <span>
              From the Boola Boola Shop
              <br />
              <strong>57 Broadway, New Haven</strong>
            </span>
          </div>
        </div>
        <div className="hero-visual">
          <span className="hero-background-y" aria-hidden="true">
            Y
          </span>
          <span className="hero-top-label">ON CAMPUS.<br />OFF DUTY.</span>
          {hero ? (
            <Link
              to={`/products/${hero.id}`}
              className="hero-product"
              aria-label={`Explore ${hero.name}`}
            >
              <ProductImage product={hero} eager />
            </Link>
          ) : (
            <span className="hero-empty">
              Bulldog blue, through and through.
            </span>
          )}
          <div className="hero-caption">
            <div>
              <span className="eyebrow">A CAMPUS CLASSIC</span>
              <h2>{hero?.name ?? "Make it Yale."}</h2>
              {hero && <p className="hero-price">{money(hero.price)} <span>YOUR EVERYDAY YALE</span></p>}
            </div>
            {hero && (
              <Link
                to={`/products/${hero.id}`}
                aria-label={`View ${hero.name}`}
                className="round-link"
              >
                <Arrow />
              </Link>
            )}
          </div>
          <span className="hero-side-label">
            CAMPUS CUSTOMS · NEW HAVEN, CT
          </span>
        </div>
      </section>
      <div className="spirit-strip">
        <span>For students & alumni</span>
        <span className="strip-star" aria-hidden="true">
          ✳
        </span>
        <span>For family & fans</span>
        <span className="strip-star" aria-hidden="true">
          ✳
        </span>
        <span>For the love of Yale</span>
      </div>
      <section className="section container">
        <div className="section-heading">
          <div>
            <p className="eyebrow">GOOD COMPANY. GREAT COLORS.</p>
            <h2>Good gear.<br />Great company.</h2>
          </div>
          <Link className="text-link" to="/products">
            Explore the collection <Arrow />
          </Link>
        </div>
        {loading ? (
          <Loading />
        ) : error ? (
          <ErrorState message={error} retry={retry} />
        ) : (
          <div className="product-grid">
            {featured.map((product) => (
              <ProductCard key={product.id} product={product} />
            ))}
          </div>
        )}
      </section>
      <section className="story-band container">
        <div className="story-monogram" aria-hidden="true">
          Y<span>57 BROADWAY</span>
        </div>
        <div>
          <p className="eyebrow">A LITTLE SHOP. A BIG BLUE HEART.</p>
          <h2>
            Consider yourself
            <br />
            part of the pack.
          </h2>
          <p>
            First semester or fiftieth reunion, proud parent or lifelong fan:
            there’s a place for you here. Campus Customs brings Yale spirit to
            everyday life, one well-loved favorite at a time.
          </p>
          <Link className="text-link" to="/about">
            Get to know the Boola Boola Shop <Arrow />
          </Link>
        </div>
      </section>
    </>
  );
}

function Products() {
  const [params, setParams] = useSearchParams();
  const search = params.get("q") ?? "";
  const sort = params.get("sort") ?? "name";
  const category = params.get("category") ?? "";
  const maxPrice = params.get("max_price") ?? "";
  const size = params.get("size") ?? "";
  const { products, loading, error, retry } = useCatalogue(category, maxPrice, size);
  const hasFilters = Boolean(search || category || maxPrice || size);
  function clearFilters() {
    const next = new URLSearchParams();
    if (sort !== "name") next.set("sort", sort);
    setParams(next, { replace: true });
  }
  const visible = products
    .filter((product) =>
      `${product.name} ${product.garment_type} ${product.short_description}`
        .toLowerCase()
        .includes(search.toLowerCase()),
    )
    .sort((a, b) =>
      sort === "price-low"
        ? a.price - b.price
        : sort === "price-high"
          ? b.price - a.price
          : a.name.localeCompare(b.name),
    );
  function updateParam(key: string, value: string) {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    setParams(next, { replace: true });
  }
  return (
    <section className="container catalogue-page">
      <div className="page-intro">
        <p className="eyebrow">WEAR YOUR BULLDOG PRIDE</p>
        <h1>The Yale collection.</h1>
        <p>From the first lecture to the last encore. Find your everyday Yale.</p>
      </div>
      <div className="catalogue-toolbar">
        <label className="search-field">
          <svg
            viewBox="0 0 24 24"
            aria-hidden="true"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.6"
          >
            <circle cx="10" cy="10" r="6" />
            <path d="m15 15 5 5" />
          </svg>
          <span className="sr-only">Search products</span>
          <input
            type="search"
            placeholder="Search hoodies, shirts, colors…"
            value={search}
            onChange={(event) => updateParam("q", event.target.value)}
          />
        </label>
        <div className="sort-field">
          <label htmlFor="product-sort">Sort by</label>
          <select
            id="product-sort"
            value={sort}
            onChange={(event) => updateParam("sort", event.target.value)}
          >
            <option value="name">Name: A–Z</option>
            <option value="price-low">Price: low to high</option>
            <option value="price-high">Price: high to low</option>
          </select>
        </div>
      </div>
      <div className="catalogue-filters" role="group" aria-label="Filter the collection">
        <label>Category
          <select value={category} onChange={(event) => updateParam("category", event.target.value)}>
            <option value="">All categories</option>
            <option value="hood">Hoodies</option>
            <option value="t-shirt">T-shirts</option>
            <option value="crewneck">Crewnecks</option>
            <option value="jacket">Jackets</option>
            <option value="quarter-zip">Quarter-zips</option>
            <option value="performance">Performance shirts</option>
            <option value="mockneck">Mocknecks</option>
          </select>
        </label>
        <label>Maximum price
          <select value={maxPrice} onChange={(event) => updateParam("max_price", event.target.value)}>
            <option value="">Any price</option>
            <option value="30">$30 or less</option>
            <option value="50">$50 or less</option>
            <option value="70">$70 or less</option>
            <option value="100">$100 or less</option>
          </select>
        </label>
        <label>In-stock size
          <select value={size} onChange={(event) => updateParam("size", event.target.value)}>
            <option value="">Any size</option>
            {["XS", "S", "M", "L", "XL", "XXL"].map((value) => <option key={value}>{value}</option>)}
          </select>
        </label>
        <button type="button" className="text-link" disabled={!hasFilters} onClick={clearFilters}>Clear filters</button>
      </div>
      {loading ? (
        <Loading />
      ) : error ? (
        <ErrorState message={error} retry={retry} />
      ) : (
        <>
          <p className="result-count" role="status">
            {visible.length} {visible.length === 1 ? "piece" : "pieces"} of Yale
          </p>
          {visible.length ? (
            <div className="product-grid">
              {visible.map((product) => (
                <ProductCard key={product.id} product={product} />
              ))}
            </div>
          ) : (
            <div className="empty-state">
              <h2>No matches just yet.</h2>
              <p>Try a different name, category, budget, or size.</p>
              <button
                className="button secondary"
                onClick={clearFilters}
              >
                Clear filters and search
              </button>
            </div>
          )}
        </>
      )}
    </section>
  );
}

function ProductRoute() {
  const { id } = useParams();
  return <ProductPage key={id} id={id ?? ""} />;
}

function ProductPage({ id }: { id: string }) {
  const [product, setProduct] = useState<ProductDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [notFound, setNotFound] = useState(false);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    getProduct(id ?? "", controller.signal)
      .then(setProduct)
      .catch((reason) => {
        if (controller.signal.aborted) return;
        if (reason instanceof ApiError && reason.status === 404)
          setNotFound(true);
        else setError("We couldn’t load this item. Please try again.");
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [id, attempt]);
  return (
    <section className="container detail-page">
      <Link className="text-link back-link" to="/products">
        <Arrow back /> Back to the collection
      </Link>
      {loading ? (
        <Loading label="Loading product details…" />
      ) : notFound ? (
        <div className="empty-state">
          <h1>That Bulldog wandered off.</h1>
          <p>
            We couldn’t find this product. Explore the collection for another
            favorite.
          </p>
          <Link className="button" to="/products">
            Browse products <Arrow />
          </Link>
        </div>
      ) : error ? (
        <ErrorState
          message={error}
          retry={() => {
            setLoading(true);
            setError("");
            setAttempt((value) => value + 1);
          }}
        />
      ) : (
        product && (
          <div className="product-detail">
            <div className="detail-photo">
              <ProductImage product={product} eager />
            </div>
            <div className="detail-copy">
              <p className="eyebrow">{product.garment_type}</p>
              <h1>{product.name}</h1>
              <p className="detail-price">{money(product.price)}</p>
              <p className="full-description">{product.description}</p>
              <div className="stock-heading">
                <h2>Sizes & availability</h2>
                <span>Current stock</span>
              </div>
              {product.sizes.length ? (
                <ul className="size-grid">
                  {product.sizes.map((item) => (
                    <li
                      key={item.size}
                      className={item.quantity <= 0 ? "sold-out" : ""}
                    >
                      <strong>{item.size}</strong>
                      <span>
                        {item.quantity <= 0
                          ? "Out of stock"
                          : `${item.quantity} in stock`}
                      </span>
                    </li>
                  ))}
                </ul>
              ) : (
                <p>
                  Size availability isn’t listed for this item. Please check
                  with the shop.
                </p>
              )}
              <p className="stock-note">
                Stock can change. Visit us at 57 Broadway to find your fit.
              </p>
              <div className="detail-bottom">
                <span className="mini-y" aria-hidden="true">
                  Y
                </span>
                <p>
                  A little Yale, wherever life takes you.
                  <br />
                  <Link to="/about">
                    Meet the Boola Boola Shop <Arrow />
                  </Link>
                </p>
              </div>
            </div>
          </div>
        )
      )}
    </section>
  );
}

function About() {
  return (
    <section className="container about-page">
      <div className="about-hero">
        <div>
          <p className="eyebrow">HELLO FROM 57 BROADWAY</p>
          <h1>
            Big Bulldog energy.
            <br />
            <em>New Haven heart.</em>
          </h1>
          <p className="lede">
            Yale has a way of staying with you. We help you wear it.
          </p>
        </div>
        <div className="about-seal" aria-hidden="true">
          Y<span>THE BOOLA BOOLA SHOP</span>
        </div>
      </div>
      <div className="about-content">
        <div>
          <p className="eyebrow">OUR CORNER OF CAMPUS</p>
          <h2>
            A warm welcome.
            <br />
            An unmistakable blue.
          </h2>
        </div>
        <div>
          <p>
            Campus Customs runs Yale Bulldog Blue at 57 Broadway in New Haven,
            Connecticut. You might know us as the Boola Boola Shop—a place for
            Bulldogs and the people cheering them on.
          </p>
          <p>
            We’re here for the student finding a first Yale hoodie, the alum
            returning to a familiar neighborhood, and the family member picking
            out a thoughtful gift. Shirts, hoodies, hats, and gifts bring a
            little school spirit to days both ordinary and unforgettable.
          </p>
        </div>
      </div>
      <div className="about-cards">
        <article>
          <span className="about-number">01 / A YALE TRADITION</span>
          <h2>The Official Y Sweater</h2>
          <p>
            Some designs feel like old friends. Our Official Y Sweater replica
            has been part of the shop’s story for more than 40 years, carrying
            that familiar Yale look from one generation to the next.
          </p>
        </article>
        <article>
          <span className="about-number">02 / MAKE IT PERSONAL</span>
          <h2>A little more you</h2>
          <p>
            Looking for something with a personal touch? Campus Customs also
            offers custom items. Because they’re made especially for you, custom
            items can’t be returned.
          </p>
        </article>
      </div>
      <div className="visit-band">
        <div>
          <p className="eyebrow">COME SAY BOOLA BOOLA</p>
          <h2>57 Broadway, New Haven, CT</h2>
          <p>
            Students, alumni, parents, relatives, fans—there’s room for the
            whole pack.
          </p>
        </div>
        <Link className="button" to="/products">
          Explore the collection <Arrow />
        </Link>
      </div>
    </section>
  );
}

function AccountForm({
  signup = false,
  user,
  onAuthenticated,
}: {
  signup?: boolean;
  user: User | null;
  onAuthenticated: (user: User) => void;
}) {
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [notice, setNotice] = useState("");
  const [serverError, setServerError] = useState("");
  const [pending, setPending] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (pending) return;
    const element = event.currentTarget;
    const fields = Object.fromEntries(
      new FormData(element).entries(),
    ) as Record<string, string>;
    const next: Record<string, string> = {};
    if (signup && !fields.first_name?.trim())
      next.first_name = "Please enter your first name.";
    if (signup && !fields.last_name?.trim())
      next.last_name = "Please enter your last name.";
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(fields.email?.trim() ?? ""))
      next.email = "Please enter a valid email address.";
    if (!fields.password?.trim()) next.password = "Please enter your password.";
    else if (signup && fields.password.length < 8)
      next.password = "Use at least 8 characters.";
    if (signup && fields.confirm_password !== fields.password)
      next.confirm_password = "Your passwords don’t match.";
    setErrors(next);
    setNotice("");
    setServerError("");
    if (Object.keys(next).length) {
      (
        element.elements.namedItem(Object.keys(next)[0]) as HTMLInputElement
      )?.focus();
      return;
    }
    setPending(true);
    try {
      const result = signup
        ? await createAccount({
            first_name: fields.first_name.trim(),
            last_name: fields.last_name.trim(),
            email: fields.email.trim(),
            password: fields.password,
            confirm_password: fields.confirm_password,
          })
        : await login(fields.email.trim(), fields.password);
      element.reset();
      setNotice(
        signup
          ? "Your account is ready. You’re logged in!"
          : "You’re logged in. Welcome back!",
      );
      onAuthenticated(result.user);
    } catch (error) {
      if (error instanceof ApiError) {
        setServerError(error.message);
        setErrors(error.fields);
      } else {
        setServerError("We couldn’t reach the shop. Please try again.");
      }
    } finally {
      // Keep credentials only in the form/request while needed, never in logs,
      // URLs, localStorage, sessionStorage, or the app's saved account state.
      for (const name of ["password", "confirm_password"]) {
        const input = element.elements.namedItem(
          name,
        ) as HTMLInputElement | null;
        if (input) input.value = "";
      }
      setPending(false);
    }
  }

  function field(
    name: string,
    label: string,
    type: string,
    autocomplete: string,
    hint?: string,
  ) {
    return (
      <div className="form-field">
        <label htmlFor={name}>{label}</label>
        <input
          id={name}
          name={name}
          type={type}
          autoComplete={autocomplete}
          required
          maxLength={name === "email" ? 254 : type === "password" ? 128 : 80}
          minLength={signup && type === "password" ? 8 : 1}
          aria-invalid={!!errors[name]}
          aria-describedby={
            errors[name] ? `${name}-error` : hint ? `${name}-hint` : undefined
          }
        />
        {hint && <small id={`${name}-hint`}>{hint}</small>}
        {errors[name] && (
          <p id={`${name}-error`} className="field-error" role="alert">
            {errors[name]}
          </p>
        )}
      </div>
    );
  }
  return (
    <section className="account-page container">
      <aside className="account-aside">
        <p className="eyebrow">YOU’RE IN GOOD COMPANY</p>
        <span className="account-y" aria-hidden="true">
          Y
        </span>
        <h2>
          Once a Bulldog.
          <br />
          Always at home.
        </h2>
        <p>A little campus connection, wherever you are.</p>
      </aside>
      <div className="account-form-wrap">
        <p className="eyebrow">CAMPUS CUSTOMS</p>
        <h1>
          {user
            ? `Welcome, ${user.first_name || user.name}.`
            : signup
              ? "Join the pack."
              : "Welcome back."}
        </h1>
        {user ? (
          <div className="signed-in-panel">
            <p className="form-status" role="status">
              {notice || "You’re logged in."}
            </p>
            <p className="signed-in-email">{user.email}</p>
            <Link className="button" to="/products">
              Explore the collection <Arrow />
            </Link>
            <p className="account-switch">
              To use another account, choose Log out above.
            </p>
          </div>
        ) : (
          <>
            <p className="form-intro">
              {signup
                ? "Make room for a little more Yale."
                : "Good to see you in Bulldog blue."}
            </p>
            <form noValidate onSubmit={submit} className="auth-form">
              <fieldset disabled={pending}>
                {signup && (
                  <div className="name-fields">
                    {field("first_name", "First name", "text", "given-name")}
                    {field("last_name", "Last name", "text", "family-name")}
                  </div>
                )}
                {field(
                  "email",
                  "Email address",
                  "email",
                  signup ? "email" : "username",
                )}
                {field(
                  "password",
                  "Password",
                  "password",
                  signup ? "new-password" : "current-password",
                  signup
                    ? "8–128 characters. Choose a password you don’t use elsewhere."
                    : undefined,
                )}
                {signup &&
                  field(
                    "confirm_password",
                    "Confirm password",
                    "password",
                    "new-password",
                  )}
                <button className="button form-submit" type="submit">
                  {pending
                    ? signup
                      ? "Creating account…"
                      : "Logging in…"
                    : signup
                      ? "Create account"
                      : "Log in"}{" "}
                  <Arrow />
                </button>
              </fieldset>
              {pending && (
                <p className="sr-only" role="status">
                  Please wait…
                </p>
              )}
              {serverError && (
                <p className="auth-error" role="alert">
                  {serverError}
                </p>
              )}
            </form>
            <p className="account-switch">
              {signup ? "Already part of the pack?" : "New around here?"}{" "}
              <Link to={signup ? "/login" : "/signup"}>
                {signup ? "Log in" : "Create an account"}
              </Link>
            </p>
          </>
        )}
      </div>
    </section>
  );
}

function Footer() {
  return (
    <footer className="site-footer">
      <div className="container footer-inner">
        <div>
          <Link to="/" className="footer-brand">
            Campus Customs
          </Link>
          <p>A little Yale goes a long way.</p>
        </div>
        <div>
          <span className="eyebrow">THE BOOLA BOOLA SHOP</span>
          <address>
            57 Broadway
            <br />
            New Haven, Connecticut
          </address>
        </div>
        <nav aria-label="Footer navigation">
          <Link to="/products">
            Shop the collection <Arrow />
          </Link>
          <Link to="/about">
            Our story <Arrow />
          </Link>
        </nav>
      </div>
      <div className="container footer-bottom">
        <span>Yale Bulldog Blue by Campus Customs</span>
        <span>Homework storefront · browsing preview</span>
      </div>
    </footer>
  );
}

export default function App() {
  const [user, setUser] = useState<User | null>(null);
  const [authLoading, setAuthLoading] = useState(true);
  useEffect(() => {
    const controller = new AbortController();
    getCurrentUser(controller.signal)
      .then((result) => {
        if (!controller.signal.aborted) setUser(result.user);
      })
      .catch(() => {
        // An absent/expired session simply leaves the visitor logged out.
      })
      .finally(() => {
        if (!controller.signal.aborted) setAuthLoading(false);
      });
    return () => controller.abort();
  }, []);
  function authenticated(account: User) {
    setUser(account);
    setAuthLoading(false);
  }
  async function signOut() {
    await logout();
    setUser(null);
  }
  return (
    <BrowserRouter>
      <RouteChange />
      <Header user={user} loading={authLoading} onLogout={signOut} />
      <main id="main-content" tabIndex={-1}>
        <Routes>
          <Route path="/" element={<Home />} />
          <Route path="/products" element={<Products />} />
          <Route path="/products/:id" element={<ProductRoute />} />
          <Route path="/about" element={<About />} />
          <Route
            path="/login"
            element={
              authLoading ? (
                <Loading label="Checking account…" />
              ) : (
                <AccountForm
                  key="login"
                  user={user}
                  onAuthenticated={authenticated}
                />
              )
            }
          />
          <Route
            path="/signup"
            element={
              authLoading ? (
                <Loading label="Checking account…" />
              ) : (
                <AccountForm
                  key="signup"
                  signup
                  user={user}
                  onAuthenticated={authenticated}
                />
              )
            }
          />
          <Route
            path="*"
            element={
              <div className="container empty-state">
                <h1>Off the beaten path?</h1>
                <p>That page isn’t here. Let’s head back to campus.</p>
                <Link className="button" to="/">
                  Back home <Arrow />
                </Link>
              </div>
            }
          />
        </Routes>
      </main>
      <Footer />
      {!authLoading && <ChatWidget key={user?.id ?? "guest"} user={user} />}
    </BrowserRouter>
  );
}
