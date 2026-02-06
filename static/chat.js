const chatForm = document.getElementById("chatForm");
const chatInput = document.getElementById("chatInput");
const chatMessages = document.getElementById("chatMessages");

function appendMessage(text, sender = "user") {
  if (!chatMessages) return;
  const wrapper = document.createElement("div");
  wrapper.classList.add("chat-message", sender);

  const bubble = document.createElement("div");
  bubble.classList.add("chat-bubble");
  bubble.innerHTML = text;

  wrapper.appendChild(bubble);
  chatMessages.appendChild(wrapper);
  chatMessages.scrollTop = chatMessages.scrollHeight;
}

if (chatForm && chatInput) {
  chatForm.addEventListener("submit", (e) => {
    e.preventDefault();
    const value = chatInput.value.trim();
    if (!value) return;

    appendMessage(value, "user");
    chatInput.value = "";

    setTimeout(() => {
      appendMessage(
        "This is a prototype response. In the final system, I will query your stored contacts via Azure and return real results based on your question.",
        "bot"
      );
    }, 500);
  });
}
