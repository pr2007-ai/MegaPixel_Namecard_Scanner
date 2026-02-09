const chatForm = document.getElementById("chatForm");
const chatInput = document.getElementById("chatInput");
const chatMessages = document.getElementById("chatMessages");

function appendMessage(text, sender = "user") {
  if (!chatMessages) return;
  const wrapper = document.createElement("div");
  wrapper.classList.add("chat-message", sender);

  const bubble = document.createElement("div");
  bubble.classList.add("chat-bubble");
  bubble.textContent = text; // safer than innerHTML
  // If you need HTML formatting, change back to innerHTML carefully.

  wrapper.appendChild(bubble);
  chatMessages.appendChild(wrapper);
  chatMessages.scrollTop = chatMessages.scrollHeight;
}

async function sendToBackend(message) {
  const res = await fetch("/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message })
  });

  // if Flask returns an error code, still try to read JSON
  const data = await res.json();
  return data.reply || "No reply returned.";
}

if (chatForm && chatInput) {
  chatForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const value = chatInput.value.trim();
    if (!value) return;

    appendMessage(value, "user");
    chatInput.value = "";

    // Optional "typing..." message
    const typingId = "typing-" + Date.now();
    appendMessage("Typing...", "bot");

    try {
      const reply = await sendToBackend(value);

      // Remove last "Typing..." bubble
      const last = chatMessages.lastElementChild;
      if (last && last.querySelector(".chat-bubble")?.textContent === "Typing...") {
        chatMessages.removeChild(last);
      }

      appendMessage(reply, "bot");
    } catch (err) {
      // Remove typing bubble if it exists
      const last = chatMessages.lastElementChild;
      if (last && last.querySelector(".chat-bubble")?.textContent === "Typing...") {
        chatMessages.removeChild(last);
      }

      appendMessage("Error: I couldn't reach the server. Is Flask running?", "bot");
      console.error(err);
    }
  });
}
