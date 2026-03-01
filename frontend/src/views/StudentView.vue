<template>
  <div class="app">
    <header class="topbar">
      <div class="brand">
        <div class="logo">CH</div>
        <div>
          <p class="eyebrow">学生工作台</p>
          <h1>欢迎，{{ auth?.name }}</h1>
          <p class="subtitle">学号：{{ auth?.student_no }}</p>
        </div>
      </div>
      <div class="status">
        <span class="badge">学生视角</span>
        <button class="btn ghost" @click="logout">退出登录</button>
      </div>
    </header>

    <section class="control-panel">
      <div class="control">
        <label>班级编号</label>
        <input v-model="classCode" placeholder="如 2502" />
      </div>
      <button class="btn primary" @click="refreshConversations">刷新会话</button>
      <span class="tip" v-if="errorMessage">{{ errorMessage }}</span>
    </section>

    <section class="main-grid">
      <aside class="panel list-panel">
        <div class="panel-header">
          <div>
            <h2>会话列表</h2>
            <p class="muted">按班级或关键词过滤会话。</p>
          </div>
          <div class="panel-actions">
            <input v-model="keyword" placeholder="搜索会话名称" />
            <button class="btn ghost" @click="refreshConversations">搜索</button>
          </div>
        </div>

        <div class="new-conversation">
          <input v-model="newConversationName" placeholder="新会话名称（可选）" />
          <button class="btn light" @click="createConversation">新增会话</button>
        </div>

        <div class="list">
          <button
            v-for="conv in conversations"
            :key="conv.conversation_id"
            class="list-item"
            :class="{ active: conv.conversation_id === selectedConversationId }"
            @click="selectConversation(conv.conversation_id)"
          >
            <div class="list-title">{{ conv.name }}</div>
            <div class="list-meta">
              <span>{{ conv.class_code }}</span>
              <span>{{ formatTime(conv.updated_at) }}</span>
            </div>
            <p class="list-snippet">
              {{ conv.last_message?.content || "暂无消息" }}
            </p>
          </button>
        </div>
      </aside>

      <section class="panel conversation-panel">
        <div class="panel-header">
          <div>
            <h2>{{ activeConversation?.name || "请选择会话" }}</h2>
            <p class="muted" v-if="activeConversation">
              班级：{{ activeConversation.class_code }} - {{ activeConversation.class_name }}
            </p>
          </div>
          <div class="panel-actions">
            <input v-model="renameValue" placeholder="新的会话名称" />
            <button class="btn light" @click="renameConversation">重命名</button>
            <button class="btn ghost" @click="clearConversation">清空</button>
            <button class="btn danger" @click="deleteConversation">删除</button>
          </div>
        </div>

        <div ref="messagesRef" class="messages">
          <div v-if="!messages.length" class="empty">
            还没有消息，发送一条开始对话吧。
          </div>
          <div
            v-for="msg in messages"
            :key="msg.id"
            class="bubble"
            :class="[msg.role, msg.pending ? 'pending' : '']"
          >
            <div class="bubble-role">{{ roleMap[msg.role] }}</div>
            <div class="bubble-content">
              {{ msg.content }}
              <span v-if="msg.pending" class="pending-dot">•</span>
              <span v-if="msg.pending" class="pending-dot">•</span>
              <span v-if="msg.pending" class="pending-dot">•</span>
            </div>
          </div>
        </div>

        <div class="composer">
          <textarea v-model="draftMessage" placeholder="输入你的问题..." :disabled="isSending" />
          <button class="btn primary" @click="sendMessage" :disabled="isSending">
            {{ isSending ? "发送中..." : "发送" }}
          </button>
        </div>
      </section>

      <aside class="panel settings-panel">
        <div class="panel-header">
          <div>
            <h2>会话设置</h2>
            <p class="muted">学生仅支持提示词与检索参数。</p>
          </div>
        </div>
        <div class="form-row">
          <label>系统提示语</label>
          <textarea v-model="settings.systemPrompt" placeholder="请用要点回答" />
          <p class="helper">系统会自动补全 {knowledge} 占位符。</p>
        </div>
        <div class="form-row">
          <label>Top-N</label>
          <input v-model.number="settings.topN" type="number" min="1" />
        </div>
        <div class="form-row">
          <label>相似度阈值</label>
          <input
            v-model.number="settings.similarityThreshold"
            type="number"
            min="0"
            max="1"
            step="0.01"
          />
        </div>
        <div class="form-row inline">
          <label>显示引用</label>
          <input v-model="settings.showCitations" type="checkbox" />
        </div>
        <button class="btn primary" @click="updateSettings">更新设置</button>
      </aside>
    </section>

    <footer class="footer">
      <span>提示：学生仅能访问本班级知识库。</span>
    </footer>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, ref } from "vue";
import { useRouter } from "vue-router";
import { request } from "../services/api";
import { clearAuth, getAuth } from "../services/auth";

interface ConversationSummary {
  conversation_id: number;
  name: string;
  class_id: number;
  class_code: string;
  class_name: string;
  updated_at: string;
  last_message?: {
    role: string;
    content: string;
    created_at: string;
  } | null;
}

interface MessageItem {
  id: number;
  role: "user" | "assistant" | "system";
  content: string;
  created_at: string;
  pending?: boolean;
}

const router = useRouter();
const auth = getAuth();
const classCode = ref("");
const keyword = ref("");
const newConversationName = ref("");
const conversations = ref<ConversationSummary[]>([]);
const selectedConversationId = ref<number | null>(null);
const messages = ref<MessageItem[]>([]);
const draftMessage = ref("");
const renameValue = ref("");
const errorMessage = ref("");
const isSending = ref(false);
const messagesRef = ref<HTMLElement | null>(null);

const settings = ref({
  systemPrompt: "",
  topN: 5,
  similarityThreshold: 0.2,
  showCitations: true,
});

const roleMap: Record<string, string> = {
  user: "用户",
  assistant: "助手",
  system: "系统",
};

const activeConversation = computed(() =>
  conversations.value.find((conv) => conv.conversation_id === selectedConversationId.value) || null
);

const formatTime = (value?: string) => {
  if (!value) return "";
  const dt = new Date(value);
  return dt.toLocaleString();
};

const scrollToBottom = async () => {
  await nextTick();
  if (messagesRef.value) {
    messagesRef.value.scrollTop = messagesRef.value.scrollHeight;
  }
};

const logout = () => {
  clearAuth();
  router.push("/login");
};

const refreshConversations = async () => {
  errorMessage.value = "";
  if (!classCode.value) {
    errorMessage.value = "请先填写班级编号";
    return;
  }
  const params = new URLSearchParams({
    role: "student",
    user_id: String(auth?.id || ""),
    class_code: classCode.value,
    include_last_message: "true",
  });
  if (keyword.value.trim()) {
    params.set("keyword", keyword.value.trim());
  }
  try {
    const data = await request<any>(`/conversations?${params.toString()}`);
    conversations.value = data.items || [];
    if (conversations.value.length > 0) {
      selectedConversationId.value = conversations.value[0].conversation_id;
      await loadConversationDetail(selectedConversationId.value);
    } else {
      selectedConversationId.value = null;
      messages.value = [];
    }
  } catch (err: any) {
    errorMessage.value = err.message || "加载会话失败";
  }
};

const createConversation = async () => {
  errorMessage.value = "";
  if (!classCode.value) {
    errorMessage.value = "请先填写班级编号";
    return;
  }
  try {
    const data = await request<any>("/conversations", {
      method: "POST",
      body: JSON.stringify({
        role: "student",
        user_id: auth?.id,
        class_code: classCode.value,
        name: newConversationName.value || undefined,
      }),
    });
    newConversationName.value = "";
    await refreshConversations();
    selectedConversationId.value = data.conversation_id;
    await loadConversationDetail(data.conversation_id);
  } catch (err: any) {
    errorMessage.value = err.message || "创建会话失败";
  }
};

const selectConversation = async (convId: number) => {
  selectedConversationId.value = convId;
  await loadConversationDetail(convId);
};

const loadConversationDetail = async (convId: number) => {
  errorMessage.value = "";
  if (!convId) return;
  try {
    const params = new URLSearchParams({
      role: "student",
      user_id: String(auth?.id || ""),
    });
    const data = await request<any>(`/conversations/${convId}?${params.toString()}`);
    messages.value = data.messages || [];
    renameValue.value = data.name || "";
    settings.value = {
      systemPrompt: data.system_prompt || "",
      topN: data.top_n || 5,
      similarityThreshold: Number(data.similarity_threshold ?? 0.2),
      showCitations: Boolean(data.show_citations),
    };
    await scrollToBottom();
  } catch (err: any) {
    errorMessage.value = err.message || "加载会话详情失败";
  }
};

const sendMessage = async () => {
  errorMessage.value = "";
  if (!selectedConversationId.value) {
    errorMessage.value = "请先选择会话";
    return;
  }
  const content = draftMessage.value.trim();
  if (!content) {
    errorMessage.value = "消息不能为空";
    return;
  }
  if (isSending.value) return;

  isSending.value = true;
  draftMessage.value = "";

  const now = new Date().toISOString();
  const tempUserId = Date.now();
  const tempAssistantId = tempUserId + 1;

  messages.value.push({
    id: tempUserId,
    role: "user",
    content,
    created_at: now,
  });
  messages.value.push({
    id: tempAssistantId,
    role: "assistant",
    content: "正在生成回复...",
    created_at: now,
    pending: true,
  });
  await scrollToBottom();

  try {
    const data = await request(`/conversations/${selectedConversationId.value}/messages`, {
      method: "POST",
      body: JSON.stringify({
        role: "student",
        user_id: auth?.id,
        content,
      }),
    });

    const idx = messages.value.findIndex((msg) => msg.id === tempAssistantId);
    if (idx >= 0) {
      messages.value[idx] = {
        ...messages.value[idx],
        id: data.assistant_message_id || tempAssistantId,
        content: data.assistant_answer || "（无回复内容）",
        pending: false,
      };
    }
    await refreshConversations();
    await loadConversationDetail(selectedConversationId.value);
  } catch (err: any) {
    const idx = messages.value.findIndex((msg) => msg.id === tempAssistantId);
    if (idx >= 0) {
      messages.value[idx] = {
        ...messages.value[idx],
        content: "请求失败，请重试。",
        pending: false,
      };
    }
    errorMessage.value = err.message || "发送失败";
  } finally {
    isSending.value = false;
    await scrollToBottom();
  }
};

const renameConversation = async () => {
  errorMessage.value = "";
  if (!selectedConversationId.value) {
    errorMessage.value = "请先选择会话";
    return;
  }
  if (!renameValue.value.trim()) {
    errorMessage.value = "名称不能为空";
    return;
  }
  try {
    await request(`/conversations/${selectedConversationId.value}/rename`, {
      method: "PUT",
      body: JSON.stringify({
        role: "student",
        user_id: auth?.id,
        new_name: renameValue.value.trim(),
        sync_ragflow: true,
        sync_session: true,
      }),
    });
    await refreshConversations();
  } catch (err: any) {
    errorMessage.value = err.message || "重命名失败";
  }
};

const clearConversation = async () => {
  errorMessage.value = "";
  if (!selectedConversationId.value) {
    errorMessage.value = "请先选择会话";
    return;
  }
  try {
    await request(`/conversations/${selectedConversationId.value}/clear`, {
      method: "POST",
      body: JSON.stringify({
        role: "student",
        user_id: auth?.id,
        sync_ragflow: true,
        reset_session: true,
      }),
    });
    await loadConversationDetail(selectedConversationId.value);
  } catch (err: any) {
    errorMessage.value = err.message || "清空失败";
  }
};

const deleteConversation = async () => {
  errorMessage.value = "";
  if (!selectedConversationId.value) {
    errorMessage.value = "请先选择会话";
    return;
  }
  try {
    await request(`/conversations/${selectedConversationId.value}`, {
      method: "DELETE",
      body: JSON.stringify({
        role: "student",
        user_id: auth?.id,
        sync_ragflow: true,
      }),
    });
    selectedConversationId.value = null;
    messages.value = [];
    await refreshConversations();
  } catch (err: any) {
    errorMessage.value = err.message || "删除失败";
  }
};

const updateSettings = async () => {
  errorMessage.value = "";
  if (!selectedConversationId.value) {
    errorMessage.value = "请先选择会话";
    return;
  }
  try {
    await request(`/conversations/${selectedConversationId.value}/settings`, {
      method: "PUT",
      body: JSON.stringify({
        role: "student",
        user_id: auth?.id,
        system_prompt: settings.value.systemPrompt || undefined,
        top_n: settings.value.topN,
        similarity_threshold: settings.value.similarityThreshold,
        show_citations: settings.value.showCitations,
        sync_ragflow: true,
      }),
    });
    await loadConversationDetail(selectedConversationId.value);
  } catch (err: any) {
    errorMessage.value = err.message || "更新设置失败";
  }
};
</script>