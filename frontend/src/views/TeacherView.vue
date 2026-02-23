<template>
  <div class="app">
    <header class="topbar">
      <div class="brand">
        <div class="logo">CH</div>
        <div>
          <p class="eyebrow">教师工作台</p>
          <h1>欢迎，{{ auth?.name }}</h1>
          <p class="subtitle">工号：{{ auth?.teacher_no }}</p>
        </div>
      </div>
      <div class="status">
        <span class="badge">教师视角</span>
        <button class="btn ghost" @click="logout">退出登录</button>
      </div>
    </header>

    <section class="control-panel">
      <div class="control">
        <label>班级</label>
        <select v-model="classCode">
          <option value="">请选择班级</option>
          <option v-for="cls in classes" :key="cls.class_code" :value="cls.class_code">
            {{ cls.class_code }}｜{{ cls.class_name }}
          </option>
        </select>
      </div>
      <button class="btn primary" @click="refreshConversations">刷新会话</button>
      <span class="tip" v-if="errorMessage">{{ errorMessage }}</span>
    </section>

    <section class="main-grid">
      <aside class="panel list-panel">
        <div class="panel-header">
          <div>
            <h2>对话列表</h2>
            <p class="muted">按班级或关键词过滤会话。</p>
          </div>
          <div class="panel-actions">
            <input v-model="keyword" placeholder="搜索对话名称" />
            <button class="btn ghost" @click="refreshConversations">搜索</button>
          </div>
        </div>

        <div class="new-conversation">
          <input v-model="newConversationName" placeholder="新对话名称（可选）" />
          <button class="btn light" @click="createConversation">新增对话</button>
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
            <h2>{{ activeConversation?.name || "请选择对话" }}</h2>
            <p class="muted" v-if="activeConversation">
              班级：{{ activeConversation.class_code }}｜{{ activeConversation.class_name }}
            </p>
          </div>
          <div class="panel-actions">
            <input v-model="renameValue" placeholder="新的对话名称" />
            <button class="btn light" @click="renameConversation">重命名</button>
            <button class="btn ghost" @click="clearConversation">清空</button>
            <button class="btn danger" @click="deleteConversation">删除</button>
          </div>
        </div>

        <div class="messages">
          <div v-if="!messages.length" class="empty">
            还没有消息，发送一条开始对话吧。
          </div>
          <div
            v-for="msg in messages"
            :key="msg.id"
            class="bubble"
            :class="msg.role"
          >
            <div class="bubble-role">{{ roleMap[msg.role] }}</div>
            <div class="bubble-content">{{ msg.content }}</div>
          </div>
        </div>

        <div class="composer">
          <textarea v-model="draftMessage" placeholder="输入你的问题..." />
          <button class="btn primary" @click="sendMessage">发送</button>
        </div>
      </section>

      <aside class="panel settings-panel">
        <div class="panel-header">
          <div>
            <h2>对话设置</h2>
            <p class="muted">修改模型与检索参数。</p>
          </div>
        </div>
        <div class="form-row">
          <label>模型名称</label>
          <input v-model="settings.modelName" placeholder="如 Qwen/Qwen2.5-7B-Instruct" />
        </div>
        <div class="form-row">
          <label>系统提示词</label>
          <textarea v-model="settings.systemPrompt" placeholder="请用要点回答" />
          <p class="helper">系统会自动补充 {knowledge} 占位符。</p>
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
      <span>提示：如无班级，请先在管理员端创建并绑定教师。</span>
    </footer>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { useRouter } from "vue-router";
import { request } from "../services/api";
import { clearAuth, getAuth } from "../services/auth";

interface ClassInfo {
  class_id: number;
  class_code: string;
  class_name: string;
}

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
}

const router = useRouter();
const auth = getAuth();
const classes = ref<ClassInfo[]>([]);
const classCode = ref("");
const keyword = ref("");
const newConversationName = ref("");
const conversations = ref<ConversationSummary[]>([]);
const selectedConversationId = ref<number | null>(null);
const messages = ref<MessageItem[]>([]);
const draftMessage = ref("");
const renameValue = ref("");
const errorMessage = ref("");

const settings = ref({
  modelName: "",
  systemPrompt: "",
  topN: 5,
  similarityThreshold: 0.2,
  showCitations: true,
});

const roleMap: Record<string, string> = {
  user: "你",
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

const logout = () => {
  clearAuth();
  router.push("/login");
};

const loadClasses = async () => {
  errorMessage.value = "";
  try {
    const data = await request<ClassInfo[]>(`/teachers/${auth?.id}/classes`);
    classes.value = data || [];
    if (classes.value.length === 1) {
      classCode.value = classes.value[0].class_code;
    }
  } catch (err: any) {
    errorMessage.value = err.message || "加载班级失败";
  }
};

const refreshConversations = async () => {
  errorMessage.value = "";
  if (!classCode.value) {
    errorMessage.value = "请先选择班级";
    return;
  }
  const params = new URLSearchParams({
    role: "teacher",
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
    errorMessage.value = err.message || "加载对话失败";
  }
};

const createConversation = async () => {
  errorMessage.value = "";
  if (!classCode.value) {
    errorMessage.value = "请先选择班级";
    return;
  }
  try {
    const data = await request<any>("/conversations", {
      method: "POST",
      body: JSON.stringify({
        role: "teacher",
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
    errorMessage.value = err.message || "创建对话失败";
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
      role: "teacher",
      user_id: String(auth?.id || ""),
    });
    const data = await request<any>(`/conversations/${convId}?${params.toString()}`);
    messages.value = data.messages || [];
    renameValue.value = data.name || "";
    settings.value = {
      modelName: data.model_name || "",
      systemPrompt: data.system_prompt || "",
      topN: data.top_n || 5,
      similarityThreshold: Number(data.similarity_threshold ?? 0.2),
      showCitations: Boolean(data.show_citations),
    };
  } catch (err: any) {
    errorMessage.value = err.message || "加载对话详情失败";
  }
};

const sendMessage = async () => {
  errorMessage.value = "";
  if (!selectedConversationId.value) {
    errorMessage.value = "请先选择对话";
    return;
  }
  if (!draftMessage.value.trim()) {
    errorMessage.value = "消息不能为空";
    return;
  }
  try {
    await request(`/conversations/${selectedConversationId.value}/messages`, {
      method: "POST",
      body: JSON.stringify({
        role: "teacher",
        user_id: auth?.id,
        content: draftMessage.value.trim(),
      }),
    });
    draftMessage.value = "";
    await loadConversationDetail(selectedConversationId.value);
    await refreshConversations();
  } catch (err: any) {
    errorMessage.value = err.message || "发送失败";
  }
};

const renameConversation = async () => {
  errorMessage.value = "";
  if (!selectedConversationId.value) {
    errorMessage.value = "请先选择对话";
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
        role: "teacher",
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
    errorMessage.value = "请先选择对话";
    return;
  }
  try {
    await request(`/conversations/${selectedConversationId.value}/clear`, {
      method: "POST",
      body: JSON.stringify({
        role: "teacher",
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
    errorMessage.value = "请先选择对话";
    return;
  }
  try {
    await request(`/conversations/${selectedConversationId.value}`, {
      method: "DELETE",
      body: JSON.stringify({
        role: "teacher",
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
    errorMessage.value = "请先选择对话";
    return;
  }
  try {
    await request(`/conversations/${selectedConversationId.value}/settings`, {
      method: "PUT",
      body: JSON.stringify({
        role: "teacher",
        user_id: auth?.id,
        model_name: settings.value.modelName || undefined,
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

onMounted(async () => {
  await loadClasses();
});
</script>
