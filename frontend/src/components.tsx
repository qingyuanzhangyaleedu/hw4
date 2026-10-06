import { useState } from "react";
import { Link } from "react-router-dom";
import type { ProductSummary } from "./api";
import { money } from "./format";

export function Arrow({ back = false }: { back?: boolean }) {
  return <span aria-hidden="true">{back ? "←" : "↗"}</span>;
}

export function ProductImage({
  product,
  eager = false,
}: {
  product: Pick<ProductSummary, "name" | "image_url">;
  eager?: boolean;
}) {
  const [failedUrl, setFailedUrl] = useState<string | null>(null);
  if (!product.image_url || failedUrl === product.image_url) {
    return (
      <div
        className="image-placeholder"
        role="img"
        aria-label={`${product.name}: image unavailable`}
      >
        <span className="placeholder-y" aria-hidden="true">
          Y
        </span>
        <span>Photo coming soon</span>
      </div>
    );
  }
  return (
    <img
      src={product.image_url}
      alt={product.name}
      loading={eager ? "eager" : "lazy"}
      decoding="async"
      onError={() => setFailedUrl(product.image_url)}
    />
  );
}

export function ProductCard({
  product,
  onNavigate,
}: {
  product: ProductSummary;
  onNavigate?: () => void;
}) {
  return (
    <Link
      to={`/products/${encodeURIComponent(product.id)}`}
      className="product-card"
      onClick={onNavigate}
    >
      <div className="product-photo">
        <ProductImage product={product} />
        <span className="card-arrow">
          <Arrow />
        </span>
      </div>
      <div className="product-card-copy">
        <p className="eyebrow">{product.garment_type}</p>
        <div className="card-title">
          <h3>{product.name}</h3>
          <span className="price">{money(product.price)}</span>
        </div>
        <p className="card-description">{product.short_description}</p>
      </div>
    </Link>
  );
}

export function Loading({ label = "Loading products…" }: { label?: string }) {
  return (
    <div className="loading-state" role="status">
      <span className="loading-label"><span className="spinner" aria-hidden="true" />{label}</span>
      {label === "Loading products…" && <div className="loading-shelf" aria-hidden="true">
        <span /><span /><span /><span />
      </div>}
    </div>
  );
}

export function ErrorState({
  message,
  retry,
}: {
  message: string;
  retry?: () => void;
}) {
  return (
    <div className="error-state" role="alert">
      <p>{message}</p>
      {retry && (
        <button className="button secondary" onClick={retry}>
          Try again
        </button>
      )}
    </div>
  );
}
