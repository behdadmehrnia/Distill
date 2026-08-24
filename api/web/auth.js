(function () {
  const nativeFetch = window.fetch.bind(window);

  window.fetch = function (url, options = {}) {
    return nativeFetch(url, { credentials: "include", ...options });
  };

  function redirectToLogin() {
    const next = encodeURIComponent(
      window.location.pathname + window.location.search
    );
    window.location.href = `/login?next=${next}`;
  }

  window.distillAuth = {
    async me() {
      const res = await nativeFetch("/auth/me", { credentials: "include" });
      if (!res.ok) return null;
      const data = await res.json();
      return data.user;
    },

    async requireAuth() {
      const user = await this.me();
      if (!user) {
        redirectToLogin();
        return null;
      }
      return user;
    },

    async login(email, password) {
      const res = await nativeFetch("/auth/login", {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, password }),
      });
      if (!res.ok) {
        let detail = "ورود ناموفق بود";
        try {
          const err = await res.json();
          if (err.detail) detail = String(err.detail);
        } catch (_) {}
        throw new Error(detail);
      }
      const data = await res.json();
      return data.user;
    },

    async register(email, password, displayName) {
      const res = await nativeFetch("/auth/register", {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          email,
          password,
          display_name: displayName || "",
        }),
      });
      if (!res.ok) {
        let detail = "ثبت‌نام ناموفق بود";
        try {
          const err = await res.json();
          if (err.detail) detail = String(err.detail);
        } catch (_) {}
        throw new Error(detail);
      }
      const data = await res.json();
      return data.user;
    },

    async logout() {
      await nativeFetch("/auth/logout", {
        method: "POST",
        credentials: "include",
      });
      window.location.href = "/login";
    },
  };
})();
