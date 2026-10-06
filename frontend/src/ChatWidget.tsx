import { useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";
import { useMatch } from "react-router-dom";
import { getChatHistory, sendChatMessage } from "./api";
import type { ProductMatch, User } from "./api";
import { ProductCard } from "./components";

interface Message {
  id: number;
  role: "user" | "assistant";
  text: string;
  products?: ProductMatch[];
}

export default function ChatWidget({ user }: { user: User | null }) {
  const page = useMatch("/products/:id");
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const [historyLoading, setHistoryLoading] = useState(Boolean(user));
  const [historyError, setHistoryError] = useState(false);
  const [historyAttempt, setHistoryAttempt] = useState(0);
  const [messages, setMessages] = useState<Message[]>([
    {
      id: 0,
      role: "assistant",
      text: `Welcome${user ? `, ${user.first_name || user.name}` : ", Bulldog"}! Tell me what you’re looking for. I can help with Yale merch, prices, and sizes.`,
    },
  ]);
  const input = useRef<HTMLInputElement>(null);
  const toggle = useRef<HTMLButtonElement>(null);
  const messageList = useRef<HTMLDivElement>(null);
  const latestMessage = useRef<HTMLDivElement>(null);
  const nextId = useRef(1);
  const sendController = useRef<AbortController | null>(null);

  useEffect(() => {
    if (!user) return;
    const controller = new AbortController();
    getChatHistory(controller.signal)
      .then(({ messages: saved }) => {
        if (controller.signal.aborted) return;
        if (saved.length) {
          setMessages(saved.map((item) => ({ id: item.id, role: item.role, text: item.content, products: item.products })));
          nextId.current = Math.max(...saved.map((item) => item.id)) + 1;
        }
      })
      .catch(() => {
        if (!controller.signal.aborted) setHistoryError(true);
      })
      .finally(() => {
        if (!controller.signal.aborted) setHistoryLoading(false);
      });
    return () => controller.abort();
  }, [user, historyAttempt]);

  // Account changes remount this widget. Cancel old requests so one account's
  // late reply can never be rendered in another account's conversation.
  useEffect(() => () => sendController.current?.abort(), []);

  useEffect(() => {
    if (open) input.current?.focus();
  }, [open]);
  useEffect(() => {
    if (open && messageList.current && latestMessage.current) {
      // Keep the new reply and first card visible rather than jumping past
      // a long result list to its final card. Scroll only the conversation.
      messageList.current.scrollTop +=
        latestMessage.current.getBoundingClientRect().top -
        messageList.current.getBoundingClientRect().top - 12;
    }
  }, [open, messages, pending, error]);

  function close() {
    setOpen(false);
    toggle.current?.focus();
  }

  const shortcuts = page?.params.id
    ? [{ label: "Check size M", message: "Is this available in size M?" },
       { label: "Check the price", message: "What is the price of this item?" },
       { label: "Describe this item", message: "Describe this item." }]
    : [{ label: "Hoodies under $70", message: "Show me hoodies for $70 or less." },
       { label: "T-shirts in M", message: "Show me T-shirts in stock in size M." }];

  async function send(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    await sendText(draft);
  }

  async function sendText(value: string) {
    const text = value.trim();
    if (!text || pending || historyLoading || historyError) return;
    setMessages((previous) => [
      ...previous,
      { id: nextId.current++, role: "user", text },
    ]);
    setDraft("");
    setError("");
    setPending(true);
    const controller = new AbortController();
    sendController.current = controller;
    try {
      const result = await sendChatMessage(text, { product_id: page?.params.id ?? null }, controller.signal);
      if (controller.signal.aborted) return;
      setMessages((previous) => [
        ...previous,
        {
          id: nextId.current++,
          role: "assistant",
          text: result.reply,
          products: result.products,
        },
      ]);
    } catch {
      if (controller.signal.aborted) return;
      setError("We couldn’t send that message. Please try again.");
      setDraft(text);
    } finally {
      if (!controller.signal.aborted) setPending(false);
    }
  }

  return (
    <div className="chat-widget">
      {open && (
        <section
          className="chat-panel"
          id="shop-chat"
          aria-labelledby="chat-title"
          onKeyDown={(event) => {
            if (event.key === "Escape") close();
          }}
        >
          <header className="chat-header">
            <div>
              <span className="eyebrow">CC / CAMPUS CUSTOMS ASSISTANT</span>
              <h2 id="chat-title">The merch desk.</h2>
            </div>
            <button
              className="icon-button"
              aria-label="Close chat"
              onClick={close}
            >
              ×
            </button>
          </header>
          <div className="chat-preview-label">
            {user ? "Your conversation is saved to your account" : "Guest chat · not saved to an account"}
          </div>
          <div
            className="chat-messages"
            role="log"
            aria-live="polite"
            aria-relevant="additions text"
            ref={messageList}
          >
            {historyLoading && <p role="status">Loading your conversation…</p>}
            {historyError && (
              <div className="chat-error" role="alert">
                Your saved conversation could not be loaded.{" "}
                <button type="button" className="text-link" onClick={() => {
                  setHistoryLoading(true);
                  setHistoryError(false);
                  setHistoryAttempt((value) => value + 1);
                }}>Try again</button>
              </div>
            )}
            {messages.map((message, index) => (
              <div
                className={`message ${message.role}${message.products?.length ? " with-products" : ""}`}
                key={message.id}
                ref={index === messages.length - 1 ? latestMessage : undefined}
              >
                <span className="message-author">
                  {message.role === "user" ? "You" : "Campus Customs"}:{" "}
                </span>
                <p>{message.text}</p>
                {!!message.products?.length && (
                  <ul className="chat-products" aria-label="Matching products">
                    {message.products.map((product) => (
                      <li key={product.id}>
                        <ProductCard product={product} onNavigate={close} />
                      </li>
                    ))}
                  </ul>
                )}
                {message.products?.length === 6 && index === messages.length - 1 && (
                  <button type="button" className="chat-more" disabled={pending || historyLoading || historyError}
                    onClick={() => void sendText("Show more matching products.")}>Show more matches →</button>
                )}
              </div>
            ))}
            {pending && (
              <p className="chat-pending" role="status">
                <span className="thinking-dots" aria-hidden="true"><i /><i /><i /></span>
                Finding your answer…
              </p>
            )}
            {error && (
              <p className="chat-error" role="alert">
                {error}
              </p>
            )}
          </div>
          <div className="chat-shortcuts" role="group" aria-label="Suggested questions">
            <span>{page?.params.id ? "Ask about this item" : "Try a quick question"}</span>
            <div>{shortcuts.map((shortcut) => (
              <button type="button" key={shortcut.label} disabled={pending || historyLoading || historyError}
                onClick={() => void sendText(shortcut.message)}>{shortcut.label}</button>
            ))}</div>
          </div>
          <form className="chat-form" onSubmit={send}>
            <label className="sr-only" htmlFor="chat-input">
              Your message
            </label>
            <input
              ref={input}
              id="chat-input"
              placeholder="Ask about Yale merch…"
              value={draft}
              maxLength={2000}
              onChange={(event) => setDraft(event.target.value)}
              readOnly={pending || historyLoading || historyError}
            />
            <button
              type="submit"
              className="send-button"
              aria-label="Send message"
              disabled={pending || historyLoading || historyError || !draft.trim()}
            >
              ↑
            </button>
          </form>
        </section>
      )}
      <button
        ref={toggle}
        className="chat-toggle"
        aria-expanded={open}
        aria-controls="shop-chat"
        onClick={() => (open ? close() : setOpen(true))}
      >
        <svg
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.6"
          aria-hidden="true"
        >
          <path d="M20 11.5a8 8 0 0 1-8 8H4l-2 2V11.5a9 9 0 0 1 18 0Z" />
          <path d="M7 10h8M7 14h5" />
        </svg>
        {open ? "Close chat" : "Let’s talk Yale"}
        <span className="chat-dot" aria-hidden="true" />
      </button>
    </div>
  );
}
