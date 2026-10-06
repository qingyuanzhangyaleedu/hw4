/** All HTTP requests live here. These types mirror backend/models.py. */
export interface ProductSummary {
  id: string;
  name: string;
  price: number;
  short_description: string;
  image_url: string | null;
  garment_type: string;
}

export interface ProductDetail extends ProductSummary {
  description: string;
  sizes: { size: string; quantity: number }[];
}

// Chat matches share the existing shop card contract and detail-page ID.
export type ProductMatch = ProductSummary;

export interface ChatResponse {
  reply: string;
  products: ProductMatch[];
}

export interface PageContext {
  product_id: string | null;
}

export interface SavedChatMessage {
  id: number;
  role: "user" | "assistant";
  content: string;
  products: ProductMatch[];
  created_at: string;
}

export interface User {
  id: number;
  name: string;
  email: string;
  first_name: string | null;
  last_name: string | null;
  created_at: string;
}

export interface AuthResponse {
  user: User;
}

export interface SignupInput {
  first_name: string;
  last_name: string;
  email: string;
  password: string;
  confirm_password: string;
}

export class ApiError extends Error {
  status: number;
  fields: Record<string, string>;
  constructor(
    message: string,
    status: number,
    fields: Record<string, string> = {},
  ) {
    super(message);
    this.status = status;
    this.fields = fields;
  }
}

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    credentials: "same-origin",
    ...options,
  });
  if (!response.ok) {
    let message = "Something went wrong. Please try again.";
    let fields: Record<string, string> = {};
    try {
      const body = await response.json();
      if (typeof body.detail === "string") message = body.detail;
      if (body.errors && typeof body.errors === "object") fields = body.errors;
    } catch {
      /* The server may return a non-JSON error. */
    }
    throw new ApiError(message, response.status, fields);
  }
  return response.json() as Promise<T>;
}

export const getProducts = (signal?: AbortSignal, filters?: { category: string; maxPrice: string; size: string }) => {
  const params = new URLSearchParams();
  if (filters?.category) params.set("category", filters.category);
  if (filters?.maxPrice) params.set("max_price", filters.maxPrice);
  if (filters?.size) params.set("size", filters.size);
  return request<ProductSummary[]>(`/api/products?${params}`, { signal });
};

export const getProduct = (id: string, signal?: AbortSignal) =>
  request<ProductDetail>(`/api/products/${encodeURIComponent(id)}`, { signal });

export const getChatHistory = (signal?: AbortSignal) =>
  request<{ messages: SavedChatMessage[] }>("/api/chat/history", { signal });

export const sendChatMessage = (message: string, pageContext: PageContext, signal?: AbortSignal) =>
  request<ChatResponse>("/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message, page_context: pageContext }),
    signal: signal ? AbortSignal.any([signal, AbortSignal.timeout(30_000)]) : AbortSignal.timeout(30_000),
  });

export const createAccount = (input: SignupInput) =>
  request<AuthResponse>("/api/auth/signup", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  });

export const login = (email: string, password: string) =>
  request<AuthResponse>("/api/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  });

export const getCurrentUser = (signal?: AbortSignal) =>
  request<AuthResponse>("/api/auth/me", { signal });

export const logout = () =>
  request<{ message: string }>("/api/auth/logout", { method: "POST" });
