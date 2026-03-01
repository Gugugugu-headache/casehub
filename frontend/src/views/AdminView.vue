<template>
  <div class="app">
    <header class="topbar">
      <div class="brand">
        <div class="logo">CH</div>
        <div>
          <p class="eyebrow">管理员工作台</p>
          <h1>欢迎，{{ auth?.name }}</h1>
          <p class="subtitle">账号：{{ auth?.admin_no }}</p>
        </div>
      </div>
      <div class="status">
        <span class="badge">管理员视角</span>
        <button class="btn ghost" @click="logout">退出登录</button>
      </div>
    </header>

    <section class="control-panel">
      <div class="control">
        <label>功能模块</label>
        <select v-model="section">
          <option value="audit">审核管理</option>
          <option value="users">用户管理</option>
          <option value="classes">班级管理</option>
          <option value="files">文件管理</option>
        </select>
      </div>
      <div v-if="section === 'audit'" class="control">
        <label>审核筛选</label>
        <select v-model="tab">
          <option value="pending">待审核</option>
          <option value="history">已审核</option>
        </select>
      </div>
      <div v-if="section === 'users'" class="control">
        <label>用户类型</label>
        <select v-model="userTab">
          <option value="teacher">教师</option>
          <option value="student">学生</option>
        </select>
      </div>
      <div v-if="section === 'users' && userTab === 'student'" class="control">
        <label>按班级筛选</label>
        <select v-model="studentClassFilter">
          <option value="">全部班级</option>
          <option v-for="cls in classOptions" :key="cls.class_code" :value="cls.class_code">
            {{ cls.class_code }} - {{ cls.class_name }}
          </option>
        </select>
      </div>
      <button class="btn primary" @click="loadData">刷新列表</button>
      <span class="tip" v-if="errorMessage">{{ errorMessage }}</span>
    </section>

    <!-- 审核管理 -->
    <section v-if="section === 'audit'" class="admin-grid">
      <div class="panel">
        <div class="panel-header">
          <div>
            <h2>{{ tab === "pending" ? "待审核文件" : "审核记录" }}</h2>
            <p class="muted">点击记录查看详情与文件预览。</p>
          </div>
        </div>

        <div class="table">
          <div class="table-row table-head">
            <span>文件</span>
            <span>班级</span>
            <span>上传人</span>
            <span>状态</span>
          </div>
          <button
            v-for="row in auditRows"
            :key="row.key"
            class="table-row table-item"
            :class="{ active: row.key === selectedKey }"
            @click="selectAuditRow(row)"
          >
            <span>{{ row.name }}</span>
            <span>{{ row.class_name }}</span>
            <span>{{ row.uploader_name || row.uploader }}</span>
            <span>{{ row.statusText }}</span>
          </button>
        </div>
      </div>

      <div class="panel">
        <div class="panel-header">
          <div>
            <h2>审核详情</h2>
            <p class="muted">支持预览、通过/拒绝。</p>
          </div>
        </div>

        <div v-if="!auditDetail" class="empty">请选择左侧记录</div>
        <div v-else class="detail">
          <div class="detail-row"><span>文件</span><strong>{{ auditDetail.document_name }}</strong></div>
          <div class="detail-row"><span>班级</span><strong>{{ auditDetail.class_name }}</strong></div>
          <div class="detail-row"><span>上传人</span><strong>{{ auditDetail.uploader }}</strong></div>
          <div class="detail-row"><span>状态</span><strong>{{ auditDetail.statusText }}</strong></div>
          <div class="detail-row"><span>时间</span><strong>{{ formatTime(auditDetail.time) }}</strong></div>

          <div class="detail-actions">
            <a
              v-if="auditDetail.document_id"
              class="btn light"
              :href="previewUrl(auditDetail.document_id)"
              target="_blank"
              rel="noreferrer"
            >
              预览
            </a>
            <a
              v-if="auditDetail.document_id"
              class="btn light"
              :href="downloadUrl(auditDetail.document_id)"
              target="_blank"
              rel="noreferrer"
            >
              下载
            </a>
            <button v-if="tab === 'pending'" class="btn primary" @click="submitDecision('approved')">
              审核通过
            </button>
            <button v-if="tab === 'pending'" class="btn danger" @click="submitDecision('rejected')">
              审核拒绝
            </button>
          </div>

          <div v-if="tab === 'pending'" class="form-row">
            <label>审核说明（可选）</label>
            <textarea v-model="decisionReason" placeholder="请输入审核说明（可选）"></textarea>
          </div>
        </div>
      </div>
    </section>

    <!-- 用户管理 -->
    <section v-else-if="section === 'users'" class="admin-grid">
      <div class="panel">
        <div class="panel-header">
          <div>
            <h2>{{ userTab === 'teacher' ? '教师列表' : '学生列表' }}</h2>
            <p class="muted">点击列表可编辑或删除。</p>
          </div>
        </div>

        <div class="table">
          <div class="table-row table-head">
            <span>编号</span>
            <span>姓名</span>
            <span>状态</span>
            <span>班级/邮箱</span>
          </div>
          <button
            v-for="row in userTab === 'teacher' ? teacherRows : studentRows"
            :key="row.id"
            class="table-row table-item"
            :class="{ active: row.id === selectedUserId }"
            @click="selectUser(row)"
          >
            <span>{{ row.no }}</span>
            <span>{{ row.name }}</span>
            <span>{{ row.status === 1 ? '启用' : '停用' }}</span>
            <span>{{ row.extra }}</span>
          </button>
        </div>
      </div>

      <div class="panel">
        <div class="panel-header">
          <div>
            <h2>用户编辑</h2>
            <p class="muted">新增或更新用户信息。</p>
          </div>
        </div>

        <div v-if="userTab === 'teacher'" class="form">
          <div class="form-row">
            <label>教师工号</label>
            <input v-model="teacherForm.teacher_no" placeholder="如 20260101" />
          </div>
          <div class="form-row">
            <label>姓名</label>
            <input v-model="teacherForm.name" />
          </div>
          <div class="form-row">
            <label>密码</label>
            <input v-model="teacherForm.password" placeholder="不填则不修改" />
          </div>
          <div class="form-row">
            <label>邮箱</label>
            <input v-model="teacherForm.email" />
          </div>
          <div class="form-row">
            <label>状态</label>
            <select v-model.number="teacherForm.status">
              <option :value="1">启用</option>
              <option :value="0">停用</option>
            </select>
          </div>
          <div class="detail-actions">
            <button class="btn primary" @click="saveTeacher">保存</button>
            <button class="btn light" @click="resetTeacherForm">清空</button>
            <button class="btn danger" @click="deleteTeacher">删除</button>
          </div>
        </div>

        <div v-else class="form">
          <div class="form-row">
            <label>学生学号</label>
            <input v-model="studentForm.student_no" placeholder="如 2502S001" />
          </div>
          <div class="form-row">
            <label>姓名</label>
            <input v-model="studentForm.name" />
          </div>
          <div class="form-row">
            <label>密码</label>
            <input v-model="studentForm.password" placeholder="不填则不修改" />
          </div>
          <div class="form-row">
            <label>班级编号</label>
            <input v-model="studentForm.class_code" placeholder="如 2502" />
          </div>
          <div class="form-row">
            <label>邮箱</label>
            <input v-model="studentForm.email" />
          </div>
          <div class="form-row">
            <label>状态</label>
            <select v-model.number="studentForm.status">
              <option :value="1">启用</option>
              <option :value="0">停用</option>
            </select>
          </div>
          <div class="detail-actions">
            <button class="btn primary" @click="saveStudent">保存</button>
            <button class="btn light" @click="resetStudentForm">清空</button>
            <button class="btn danger" @click="deleteStudent">删除</button>
          </div>
        </div>
      </div>
    </section>

    <!-- 班级管理 -->
    <section v-else-if="section === 'classes'" class="admin-grid">
      <div class="panel">
        <div class="panel-header">
          <div>
            <h2>班级列表</h2>
            <p class="muted">点击列表可编辑或删除。</p>
          </div>
        </div>

        <div class="table">
          <div class="table-row table-head">
            <span>班级编号</span>
            <span>班级名称</span>
            <span>教师工号</span>
            <span>教师姓名</span>
          </div>
          <button
            v-for="row in classRows"
            :key="row.class_id"
            class="table-row table-item"
            :class="{ active: row.class_id === selectedClassId }"
            @click="selectClass(row)"
          >
            <span>{{ row.class_code }}</span>
            <span>{{ row.class_name }}</span>
            <span>{{ row.teacher_no }}</span>
            <span>{{ row.teacher_name }}</span>
          </button>
        </div>
      </div>

      <div class="panel">
        <div class="panel-header">
          <div>
            <h2>班级编辑</h2>
            <p class="muted">新增班级需要填写嵌入模型。</p>
          </div>
        </div>

        <div class="form">
          <div class="form-row">
            <label>班级编号</label>
            <input v-model="classForm.class_code" placeholder="如 2502" />
          </div>
          <div class="form-row">
            <label>班级名称</label>
            <input v-model="classForm.class_name" />
          </div>
          <div class="form-row">
            <label>教师工号</label>
            <input v-model="classForm.teacher_no" placeholder="如 20260101" />
          </div>
          <div class="form-row">
            <label>Embedding 模型</label>
            <input v-model="classForm.embedding_model" placeholder="如 BAAI/bge-large-zh-v1.5@BAAI" />
          </div>
          <div class="form-row">
            <label>描述（可选）</label>
            <input v-model="classForm.description" placeholder="如：本班级知识库" />
          </div>
          <div class="form-row">
            <label>切片方式（可选）</label>
            <select v-model="classForm.chunk_method">
              <option value="table">table</option>
              <option value="paragraph">paragraph</option>
            </select>
          </div>
          <div class="detail-actions">
            <button class="btn primary" @click="saveClass">保存</button>
            <button class="btn light" @click="resetClassForm">清空</button>
            <button class="btn danger" @click="deleteClass">删除</button>
          </div>
        </div>
      </div>
    </section>

    <!-- 文件管理 -->
    <section v-else class="panel">
      <div class="panel-header">
        <div>
          <h2>文件管理</h2>
          <p class="muted">按班级查询文件，支持上传/重命名/删除/查找。</p>
        </div>
      </div>

      <div class="form-row">
        <label>班级编号</label>
        <select v-model="fileClassCode">
          <option value="">请选择班级</option>
          <option v-for="cls in classOptions" :key="cls.class_code" :value="cls.class_code">
            {{ cls.class_code }} - {{ cls.class_name }}
          </option>
        </select>
      </div>
      <div class="form-row">
        <label>文件名搜索</label>
        <input v-model="fileKeyword" placeholder="输入文件名关键词" />
      </div>
      <div class="detail-actions">
        <button class="btn primary" @click="loadFiles">查询文件</button>
      </div>

      <div class="form-row">
        <label>上传文件（Excel）</label>
        <input type="file" accept=".xlsx,.xls" @change="onFileChange" />
      </div>
      <div class="detail-actions">
        <button class="btn light" @click="uploadFile">上传到班级知识库</button>
      </div>

      <div v-if="fileRows.length" class="table">
        <div class="table-row table-head">
          <span>文件</span>
          <span>班级</span>
          <span>状态</span>
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
          <span>{{ row.class_name }}</span>
          <span>{{ row.status }}</span>
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
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { useRouter } from "vue-router";
import { request, getApiBase } from "../services/api";
import { clearAuth, getAuth } from "../services/auth";

type TabType = "pending" | "history";
type SectionType = "audit" | "users" | "classes" | "files";
type UserTab = "teacher" | "student";

const router = useRouter();
const auth = getAuth();

const section = ref<SectionType>("audit");
const tab = ref<TabType>("pending");
const userTab = ref<UserTab>("teacher");
const errorMessage = ref("");

const auditRows = ref<any[]>([]);
const selectedKey = ref<string | null>(null);
const auditDetail = ref<any | null>(null);
const decisionReason = ref("");

const teacherRows = ref<any[]>([]);
const studentRows = ref<any[]>([]);
const selectedUserId = ref<number | null>(null);
const classOptions = ref<any[]>([]);
const studentClassFilter = ref("");
const teacherForm = ref({
  id: null as number | null,
  teacher_no: "",
  name: "",
  password: "",
  email: "",
  status: 1,
});
const studentForm = ref({
  id: null as number | null,
  student_no: "",
  name: "",
  password: "",
  class_code: "",
  email: "",
  status: 1,
});

const classRows = ref<any[]>([]);
const selectedClassId = ref<number | null>(null);
const classForm = ref({
  class_id: null as number | null,
  class_code: "",
  class_name: "",
  teacher_no: "",
  embedding_model: "",
  description: "",
  chunk_method: "table",
});

const fileClassCode = ref("");
const fileRows = ref<any[]>([]);
const fileKeyword = ref("");
const selectedFileId = ref<number | null>(null);
const selectedFile = ref<any | null>(null);
const fileRename = ref("");
const fileToUpload = ref<File | null>(null);

const adminId = computed(() => auth?.id || 0);
const apiBase = getApiBase();

const logout = () => {
  clearAuth();
  router.push("/login");
};

const formatTime = (value?: string) => {
  if (!value) return "";
  const dt = new Date(value);
  return dt.toLocaleString();
};

const previewUrl = (docId: number) =>
  `${apiBase}/documents/${docId}/content?role=admin&user_id=${adminId.value}`;
const downloadUrl = (docId: number) =>
  `${apiBase}/documents/${docId}/content?role=admin&user_id=${adminId.value}&download=true`;

const buildUploaderShort = (uploader?: any) => (uploader?.name ? uploader.name : "未知");
const buildUploaderFull = (uploader?: any) =>
  uploader?.name ? `${uploader.name}（${uploader.role}#${uploader.no}）` : "未知";

const loadPending = async () => {
  const data = await request<any[]>("/audits/pending");
  auditRows.value = (data || []).map((item) => ({
    key: `pending-${item.id}`,
    source: "pending",
    document_id: item.id,
    name: item.original_name,
    class_name: item.class_name || item.class_code || `班级${item.kb_id}`,
    statusText: "待审核",
    time: item.uploaded_at,
    uploader: item.uploader ? `${item.uploader.role}#${item.uploader.no}` : "未知",
    uploader_name: buildUploaderShort(item.uploader),
  }));
};

const loadHistory = async () => {
  const params = new URLSearchParams({
    admin_id: String(adminId.value),
    page: "1",
    page_size: "20",
  });
  const data = await request<any>(`/audits?${params.toString()}`);
  auditRows.value = (data.items || []).map((item: any) => ({
    key: `history-${item.audit_id}`,
    source: "history",
    audit_id: item.audit_id,
    document_id: item.document_id,
    name: item.document_name,
    class_name: item.class_name || item.class_code || "",
    statusText: item.decision === "approved" ? "已通过" : "已拒绝",
    time: item.decided_at,
    uploader: item.uploader ? `${item.uploader.role}#${item.uploader.no}` : "未知",
    uploader_name: buildUploaderShort(item.uploader),
  }));
};

const loadTeachers = async () => {
  const params = new URLSearchParams({ admin_id: String(adminId.value) });
  const data = await request<any[]>(`/admin/teachers?${params.toString()}`);
  teacherRows.value = (data || []).map((t) => ({
    id: t.id,
    no: t.teacher_no,
    name: t.name,
    status: t.status,
    extra: t.email || "-",
  }));
};

const loadStudents = async () => {
  const params = new URLSearchParams({ admin_id: String(adminId.value) });
  if (studentClassFilter.value) {
    params.set("class_code", studentClassFilter.value);
  }
  const data = await request<any[]>(`/admin/students?${params.toString()}`);
  studentRows.value = (data || []).map((s) => ({
    id: s.id,
    no: s.student_no,
    name: s.name,
    status: s.status,
    extra: s.class_name || s.class_code || "-",
    class_code: s.class_code,
    email: s.email || "",
  }));
};

const loadClasses = async () => {
  const params = new URLSearchParams({ admin_id: String(adminId.value) });
  const data = await request<any[]>(`/admin/classes?${params.toString()}`);
  classRows.value = data || [];
};

const loadClassOptions = async () => {
  const params = new URLSearchParams({ admin_id: String(adminId.value) });
  const data = await request<any[]>(`/admin/classes?${params.toString()}`);
  classOptions.value = data || [];
};

const loadData = async () => {
  errorMessage.value = "";
  auditDetail.value = null;
  selectedKey.value = null;
  try {
    if (section.value === "audit") {
      if (tab.value === "pending") {
        await loadPending();
      } else {
        await loadHistory();
      }
    } else if (section.value === "users") {
      if (userTab.value === "teacher") {
        await loadTeachers();
      } else {
        await loadStudents();
      }
    } else if (section.value === "classes") {
      await loadClasses();
    } else if (section.value === "files") {
      await loadFiles();
    }
  } catch (err: any) {
    errorMessage.value = err.message || "加载失败";
  }
};

const selectAuditRow = async (row: any) => {
  selectedKey.value = row.key;
  decisionReason.value = "";
  if (row.source === "pending") {
    auditDetail.value = {
      document_id: row.document_id,
      document_name: row.name,
      class_name: row.class_name,
      uploader: row.uploader_name ? `${row.uploader_name}（${row.uploader}）` : row.uploader,
      statusText: row.statusText,
      time: row.time,
    };
    return;
  }
  try {
    const params = new URLSearchParams({ admin_id: String(adminId.value) });
    const data = await request<any>(`/audits/${row.audit_id}?${params.toString()}`);
    auditDetail.value = {
      document_id: data.document.document_id,
      document_name: data.document.document_name,
      class_name: data.document.class_name,
      uploader: buildUploaderFull(data.uploader),
      statusText: data.decision === "approved" ? "已通过" : "已拒绝",
      time: data.decided_at,
    };
  } catch (err: any) {
    errorMessage.value = err.message || "加载详情失败";
  }
};

const submitDecision = async (decision: "approved" | "rejected") => {
  if (!auditDetail.value?.document_id) return;
  try {
    await request(`/audits/${auditDetail.value.document_id}/decision`, {
      method: "POST",
      body: JSON.stringify({
        reviewer_admin_id: adminId.value,
        decision,
        reason: decisionReason.value || undefined,
      }),
    });
    await loadData();
  } catch (err: any) {
    errorMessage.value = err.message || "提交失败";
  }
};

const selectUser = (row: any) => {
  selectedUserId.value = row.id;
  if (userTab.value === "teacher") {
    teacherForm.value = {
      id: row.id,
      teacher_no: row.no,
      name: row.name,
      password: "",
      email: row.extra === "-" ? "" : row.extra,
      status: row.status,
    };
  } else {
    studentForm.value = {
      id: row.id,
      student_no: row.no,
      name: row.name,
      password: "",
      class_code: row.class_code || "",
      email: row.email || "",
      status: row.status,
    };
  }
};

const resetTeacherForm = () => {
  selectedUserId.value = null;
  teacherForm.value = {
    id: null,
    teacher_no: "",
    name: "",
    password: "",
    email: "",
    status: 1,
  };
};

const resetStudentForm = () => {
  selectedUserId.value = null;
  studentForm.value = {
    id: null,
    student_no: "",
    name: "",
    password: "",
    class_code: "",
    email: "",
    status: 1,
  };
};

const saveTeacher = async () => {
  try {
    if (teacherForm.value.id) {
      await request(`/admin/teachers/${teacherForm.value.id}`, {
        method: "PUT",
        body: JSON.stringify({
          admin_id: adminId.value,
          name: teacherForm.value.name,
          password: teacherForm.value.password || undefined,
          email: teacherForm.value.email || undefined,
          status: teacherForm.value.status,
        }),
      });
    } else {
      await request("/admin/teachers", {
        method: "POST",
        body: JSON.stringify({
          admin_id: adminId.value,
          teacher_no: teacherForm.value.teacher_no,
          name: teacherForm.value.name,
          password: teacherForm.value.password,
          email: teacherForm.value.email || undefined,
          status: teacherForm.value.status,
        }),
      });
    }
    resetTeacherForm();
    await loadTeachers();
  } catch (err: any) {
    errorMessage.value = err.message || "保存失败";
  }
};

const deleteTeacher = async () => {
  if (!teacherForm.value.id) return;
  try {
    const params = new URLSearchParams({ admin_id: String(adminId.value) });
    await request(`/admin/teachers/${teacherForm.value.id}?${params.toString()}`, {
      method: "DELETE",
    });
    resetTeacherForm();
    await loadTeachers();
  } catch (err: any) {
    errorMessage.value = err.message || "删除失败";
  }
};

const saveStudent = async () => {
  try {
    if (studentForm.value.id) {
      await request(`/admin/students/${studentForm.value.id}`, {
        method: "PUT",
        body: JSON.stringify({
          admin_id: adminId.value,
          name: studentForm.value.name,
          password: studentForm.value.password || undefined,
          class_code: studentForm.value.class_code || undefined,
          email: studentForm.value.email || undefined,
          status: studentForm.value.status,
        }),
      });
    } else {
      await request("/admin/students", {
        method: "POST",
        body: JSON.stringify({
          admin_id: adminId.value,
          student_no: studentForm.value.student_no,
          name: studentForm.value.name,
          password: studentForm.value.password,
          class_code: studentForm.value.class_code,
          email: studentForm.value.email || undefined,
          status: studentForm.value.status,
        }),
      });
    }
    resetStudentForm();
    await loadStudents();
  } catch (err: any) {
    errorMessage.value = err.message || "保存失败";
  }
};

const deleteStudent = async () => {
  if (!studentForm.value.id) return;
  try {
    const params = new URLSearchParams({ admin_id: String(adminId.value) });
    await request(`/admin/students/${studentForm.value.id}?${params.toString()}`, {
      method: "DELETE",
    });
    resetStudentForm();
    await loadStudents();
  } catch (err: any) {
    errorMessage.value = err.message || "删除失败";
  }
};

const selectClass = (row: any) => {
  selectedClassId.value = row.class_id;
  classForm.value = {
    class_id: row.class_id,
    class_code: row.class_code,
    class_name: row.class_name,
    teacher_no: row.teacher_no,
    embedding_model: "",
    description: "",
    chunk_method: "table",
  };
};

const resetClassForm = () => {
  selectedClassId.value = null;
  classForm.value = {
    class_id: null,
    class_code: "",
    class_name: "",
    teacher_no: "",
    embedding_model: "",
    description: "",
    chunk_method: "table",
  };
};

const saveClass = async () => {
  try {
    if (classForm.value.class_id) {
      await request(`/admin/classes/${classForm.value.class_id}`, {
        method: "PUT",
        body: JSON.stringify({
          admin_id: adminId.value,
          class_name: classForm.value.class_name || undefined,
          teacher_no: classForm.value.teacher_no || undefined,
        }),
      });
    } else {
      await request("/admin/classes", {
        method: "POST",
        body: JSON.stringify({
          admin_id: adminId.value,
          class_code: classForm.value.class_code,
          class_name: classForm.value.class_name,
          teacher_no: classForm.value.teacher_no,
          embedding_model: classForm.value.embedding_model,
          description: classForm.value.description || undefined,
          chunk_method: classForm.value.chunk_method || undefined,
        }),
      });
    }
    resetClassForm();
    await loadClasses();
    await loadClassOptions();
  } catch (err: any) {
    errorMessage.value = err.message || "保存失败";
  }
};

const deleteClass = async () => {
  if (!classForm.value.class_id) return;
  try {
    const params = new URLSearchParams({ admin_id: String(adminId.value) });
    await request(`/admin/classes/${classForm.value.class_id}?${params.toString()}`, {
      method: "DELETE",
    });
    resetClassForm();
    await loadClasses();
    await loadClassOptions();
  } catch (err: any) {
    errorMessage.value = err.message || "删除失败";
  }
};

const loadFiles = async () => {
  errorMessage.value = "";
  fileRows.value = [];
  selectedFileId.value = null;
  selectedFile.value = null;
  fileRename.value = "";
  if (!fileClassCode.value.trim()) {
    errorMessage.value = "请先选择班级";
    return;
  }
  const params = new URLSearchParams({
    role: "admin",
    user_id: String(adminId.value),
    class_code: fileClassCode.value.trim(),
    page: "1",
    page_size: "50",
  });
  if (fileKeyword.value.trim()) {
    params.set("filename", fileKeyword.value.trim());
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
  if (!fileClassCode.value.trim()) {
    errorMessage.value = "请先选择班级";
    return;
  }
  if (!fileToUpload.value) {
    errorMessage.value = "请选择要上传的文件";
    return;
  }
  const form = new FormData();
  form.append("role", "admin");
  form.append("uploader_id", String(adminId.value));
  form.append("class_code", fileClassCode.value.trim());
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
        role: "admin",
        user_id: adminId.value,
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
        role: "admin",
        user_id: adminId.value,
        sync_ragflow: true,
        remove_minio: true,
      }),
    });
    await loadFiles();
  } catch (err: any) {
    errorMessage.value = err.message || "删除失败";
  }
};

watch(section, () => {
  loadData();
});

watch(tab, () => {
  if (section.value === "audit") {
    loadData();
  }
});

watch(userTab, () => {
  if (section.value === "users") {
    loadData();
  }
});

watch(studentClassFilter, () => {
  if (section.value === "users" && userTab.value === "student") {
    loadStudents();
  }
});

onMounted(loadData);
onMounted(loadClassOptions);
</script>