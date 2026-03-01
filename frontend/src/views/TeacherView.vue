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
        <label>功能模块</label>
        <select v-model="section">
          <option value="conversation">会话管理</option>
          <option value="embedding">嵌入管理</option>
          <option value="files">文件管理</option>
          <option value="search">搜索管理</option>
        </select>
      </div>
      <div class="control">
        <label>班级</label>
        <select v-model="classCode">
          <option value="">请选择班级</option>
          <option v-for="cls in classes" :key="cls.class_code" :value="cls.class_code">
            {{ cls.class_code }} - {{ cls.class_name }}
          </option>
        </select>
      </div>
      <button v-if="section === 'conversation'" class="btn primary" @click="refreshConversations">刷新会话</button>
      <button v-else-if="section === 'search'" class="btn primary" @click="runSearch">搜索</button>
      <button v-else class="btn primary" @click="loadFiles">刷新文件</button>
      <span class="tip" v-if="errorMessage">{{ errorMessage }}</span>
    </section>

    <!-- 会话管理 -->
    <section v-if="section === 'conversation'" class="main-grid">
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
            <p class="muted">修改模型与检索参数。</p>
          </div>
        </div>
        <div class="form-row">
          <label>模型名称</label>
          <input v-model="settings.modelName" placeholder="如 Qwen/Qwen2.5-7B-Instruct" />
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

    <!-- 嵌入管理 -->
    <section v-else-if="section === 'embedding'" class="panel">
      <div class="panel-header">
        <div>
          <h2>嵌入管理</h2>
          <p class="muted">教师只需选择班级文件是否执行嵌入。</p>
        </div>
      </div>

      <div class="form-row">
        <label>文件名搜索</label>
        <input v-model="fileKeyword" placeholder="输入文件名关键词" />
      </div>
      <div class="form-row">
        <label>状态过滤</label>
        <select v-model="fileStatusFilter">
          <option value="">全部（已通过/已嵌入）</option>
          <option value="approved">仅已通过</option>
          <option value="embedded">仅已嵌入</option>
        </select>
      </div>
      <div class="detail-actions">
        <button class="btn primary" @click="loadFiles">查询文件</button>
      </div>

      <div v-if="fileRows.length" class="table">
        <div class="table-row table-head">
          <span>文件</span>
          <span>状态</span>
          <span>上传时间</span>
          <span>操作</span>
        </div>
        <div v-for="row in fileRows" :key="row.document_id" class="table-row table-item">
          <span>{{ row.document_name }}</span>
          <span>{{ row.status }}</span>
          <span>{{ formatTime(row.uploaded_at) }}</span>
          <span class="file-actions">
            <button
              v-if="row.status === 'approved'"
              class="btn light"
              @click="runEmbedding(row.document_id)"
            >
              开始嵌入
            </button>
            <span v-else class="muted">已嵌入</span>
          </span>
        </div>
      </div>
      <div v-else class="empty">暂无文件数据</div>
    </section>

    <!-- 文件管理 -->
    <section v-else-if="section === 'files'" class="panel">
      <div class="panel-header">
        <div>
          <h2>文件管理</h2>
          <p class="muted">上传、重命名、删除班级知识库文件。</p>
        </div>
      </div>

      <div class="form-row">
        <label>文件名搜索</label>
        <input v-model="fileKeyword" placeholder="输入文件名关键词" />
      </div>
      <div class="form-row">
        <label>状态过滤</label>
        <select v-model="fileStatusFilter">
          <option value="">全部（已通过/已嵌入）</option>
          <option value="approved">仅已通过</option>
          <option value="embedded">仅已嵌入</option>
        </select>
      </div>
      <div class="detail-actions">
        <button class="btn primary" @click="loadFiles">查询文件</button>
      </div>

      <div class="form-row">
        <label>上传文件（Excel）</label>
        <input ref="fileInput" type="file" accept=".xlsx,.xls" @change="onFileChange" />
      </div>
      <div class="detail-actions">
        <button class="btn light" @click="uploadFile">上传到班级知识库</button>
      </div>

      <div v-if="fileRows.length" class="table">
        <div class="table-row table-head">
          <span>文件</span>
          <span>状态</span>
          <span>上传时间</span>
          <span>操作</span>
        </div>
        <button
          v-for="row in fileRows"
          :key="row.document_id"
          class="table-row table-item"
          :class="{ active: row.document_id === selectedFileId }"
          @click="selectFile(row)"
        >
          <span>{{ row.document_name }}</span>
          <span>{{ row.status }}</span>
          <span>{{ formatTime(row.uploaded_at) }}</span>
          <span class="file-actions">
            <a class="btn light" :href="previewUrl(row.document_id)" target="_blank" rel="noreferrer">预览</a>
            <a class="btn light" :href="downloadUrl(row.document_id)" target="_blank" rel="noreferrer">下载</a>
          </span>
        </button>
      </div>
      <div v-else class="empty">暂无文件数据</div>

      <div v-if="selectedFile" class="detail">
        <div class="detail-row"><span>当前文件</span><strong>{{ selectedFile.document_name }}</strong></div>
        <div class="form-row">
          <label>新文件名</label>
          <input v-model="fileRename" placeholder="如：新文件名.xlsx" />
        </div>
        <div class="detail-actions">
          <button class="btn primary" @click="renameFile">重命名</button>
          <button class="btn danger" @click="deleteFile">删除</button>
        </div>
      </div>
    </section>

    <!-- 搜索管理 -->
    <section v-else class="panel">
      <div class="panel-header">
        <div>
          <h2>搜索管理</h2>
          <p class="muted">基于班级知识库检索案例。</p>
        </div>
      </div>

      <div class="form-row">
        <label>检索问题</label>
        <input v-model="searchQuery" placeholder="输入要检索的内容" />
      </div>
      <div class="form-row">
        <label>Top-K</label>
        <input v-model.number="searchTopK" type="number" min="1" max="50" />
      </div>
      <div class="form-row">
        <label>相似度阈值</label>
        <input
          v-model.number="searchThreshold"
          type="number"
          min="0"
          max="1"
          step="0.01"
        />
      </div>
      <div class="detail-actions">
        <button class="btn primary" @click="runSearch">开始搜索</button>
      </div>

      <div v-if="searchResults.length" class="table">
        <div class="table-row table-head">
          <span>结果</span>
          <span>文件</span>
          <span>分数</span>
          <span>操作</span>
        </div>
        <div v-for="item in searchResults" :key="item.rank" class="table-row table-item">
          <span class="list-snippet">{{ item.highlight || item.content }}</span>
          <span>{{ item.document_name || "未知文件" }}</span>
          <span>{{ item.score?.toFixed ? item.score.toFixed(3) : item.score }}</span>
          <span class="file-actions">
            <a
              v-if="item.document_id"
              class="btn light"
              :href="previewUrl(item.document_id)"
              target="_blank"
              rel="noreferrer"
            >
              预览
            </a>
            <a
              v-if="item.document_id"
              class="btn light"
              :href="downloadUrl(item.document_id)"
              target="_blank"
              rel="noreferrer"
            >
              下载
            </a>
          </span>
        </div>
      </div>
      <div v-else class="empty">暂无搜索结果</div>
    </section>

    <footer class="footer">
      <span>提示：如无班级，请先在管理员端创建并绑定教师。</span>
    </footer>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from "vue";
import { useRouter } from "vue-router";
import { request, getApiBase } from "../services/api";
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
  pending?: boolean;
}

const router = useRouter();
const auth = getAuth();
const apiBase = getApiBase();

const section = ref<"conversation" | "embedding" | "files" | "search">("conversation");
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
const isSending = ref(false);
const messagesRef = ref<HTMLElement | null>(null);

const settings = ref({
  modelName: "",
  systemPrompt: "",
  topN: 5,
  similarityThreshold: 0.2,
  showCitations: true,
});

const fileRows = ref<any[]>([]);
const fileKeyword = ref("");
const fileStatusFilter = ref("");
const selectedFileId = ref<number | null>(null);
const selectedFile = ref<any | null>(null);
const fileRename = ref("");
const fileToUpload = ref<File | null>(null);
const fileInput = ref<HTMLInputElement | null>(null);

const searchQuery = ref("");
const searchTopK = ref(5);
const searchThreshold = ref(0.2);
const searchResults = ref<any[]>([]);

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

const previewUrl = (docId: number) =>
  `${apiBase}/documents/${docId}/content?role=teacher&user_id=${auth?.id}`;
const downloadUrl = (docId: number) =>
  `${apiBase}/documents/${docId}/content?role=teacher&user_id=${auth?.id}&download=true`;

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
    errorMessage.value = err.message || "加载会话失败";
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
        role: "teacher",
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
    errorMessage.value = "请先选择会话";
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
    errorMessage.value = "请先选择会话";
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
    errorMessage.value = "请先选择会话";
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

const loadFiles = async () => {
  errorMessage.value = "";
  if (!classCode.value) {
    errorMessage.value = "请先选择班级";
    return;
  }
  const params = new URLSearchParams({
    role: "teacher",
    user_id: String(auth?.id || ""),
    class_code: classCode.value,
    page: "1",
    page_size: "50",
  });
  if (fileKeyword.value.trim()) {
    params.set("filename", fileKeyword.value.trim());
  }
  if (fileStatusFilter.value) {
    params.set("status", fileStatusFilter.value);
  }
  try {
    const data = await request<any>(`/documents?${params.toString()}`);
    fileRows.value = data.items || [];
  } catch (err: any) {
    errorMessage.value = err.message || "查询文件失败";
  }
};

const onFileChange = (event: Event) => {
  const input = event.target as HTMLInputElement;
  fileToUpload.value = input.files?.[0] || null;
};

const selectFile = (row: any) => {
  selectedFileId.value = row.document_id;
  selectedFile.value = row;
  fileRename.value = row.document_name || "";
};

const uploadFile = async () => {
  errorMessage.value = "";
  if (!classCode.value) {
    errorMessage.value = "请先选择班级";
    return;
  }
  if (!fileToUpload.value) {
    errorMessage.value = "请选择要上传的文件";
    return;
  }
  const form = new FormData();
  form.append("role", "teacher");
  form.append("uploader_id", String(auth?.id));
  form.append("class_code", classCode.value);
  form.append("file", fileToUpload.value);
  try {
    const resp = await fetch(`${apiBase}/documents/upload`, {
      method: "POST",
      body: form,
    });
    if (!resp.ok) {
      const data = await resp.json().catch(() => ({}));
      throw new Error(data.detail || `文件上传失败: ${resp.status}`);
    }
    fileToUpload.value = null;
    if (fileInput.value) {
      fileInput.value.value = "";
    }
    await loadFiles();
  } catch (err: any) {
    errorMessage.value = err.message || "文件上传失败";
  }
};

const renameFile = async () => {
  if (!selectedFile.value) return;
  const newName = (fileRename.value || "").trim();
  if (!newName) {
    errorMessage.value = "请输入新文件名";
    return;
  }
  try {
    await request(`/documents/${selectedFile.value.document_id}/rename`, {
      method: "PUT",
      body: JSON.stringify({
        role: "teacher",
        user_id: auth?.id,
        new_name: newName,
        sync_ragflow: true,
      }),
    });
    await loadFiles();
  } catch (err: any) {
    errorMessage.value = err.message || "重命名失败";
  }
};

const deleteFile = async () => {
  if (!selectedFile.value) return;
  try {
    await request(`/documents/${selectedFile.value.document_id}`, {
      method: "DELETE",
      body: JSON.stringify({
        role: "teacher",
        user_id: auth?.id,
        sync_ragflow: true,
        remove_minio: true,
      }),
    });
    await loadFiles();
  } catch (err: any) {
    errorMessage.value = err.message || "删除失败";
  }
};

const runEmbedding = async (documentId: number) => {
  errorMessage.value = "";
  try {
    await request(`/embeddings/${documentId}/run`, {
      method: "POST",
      body: JSON.stringify({
        teacher_id: auth?.id,
      }),
    });
    await loadFiles();
  } catch (err: any) {
    errorMessage.value = err.message || "嵌入失败";
  }
};

const runSearch = async () => {
  errorMessage.value = "";
  searchResults.value = [];
  if (!classCode.value) {
    errorMessage.value = "请先选择班级";
    return;
  }
  if (!searchQuery.value.trim()) {
    errorMessage.value = "请输入检索问题";
    return;
  }
  try {
    const data = await request<any>("/search", {
      method: "POST",
      body: JSON.stringify({
        query: searchQuery.value.trim(),
        role: "teacher",
        user_id: auth?.id,
        class_code: classCode.value,
        top_k: searchTopK.value,
        similarity_threshold: searchThreshold.value,
        highlight: true,
      }),
    });
    searchResults.value = data.chunks || [];
  } catch (err: any) {
    errorMessage.value = err.message || "搜索失败";
  }
};

watch(classCode, () => {
  if (!classCode.value) return;
  if (section.value === "conversation") {
    refreshConversations();
  } else if (section.value === "search") {
    searchResults.value = [];
  } else {
    loadFiles();
  }
});

watch(section, () => {
  if (!classCode.value) return;
  if (section.value === "conversation") {
    refreshConversations();
  } else if (section.value === "search") {
    searchResults.value = [];
  } else {
    loadFiles();
  }
});

onMounted(async () => {
  await loadClasses();
});
</script>