<template>
  <div class="login-page">
    <div class="login-card">
      <div class="login-brand">
        <div class="logo">CH</div>
        <div>
          <p class="eyebrow">CaseHub 登录</p>
          <h1>选择身份进入系统</h1>
          <p class="muted">教师使用工号登录，学生使用学号登录。</p>
        </div>
      </div>

      <div class="form">
        <div class="form-row">
          <label>身份</label>
          <select v-model="role">
            <option value="teacher">教师</option>
            <option value="student">学生</option>
            <option value="admin">管理员</option>
          </select>
        </div>
        <div class="form-row">
          <label>{{ accountLabel }}</label>
          <input v-model="account" placeholder="请输入账号" />
        </div>
        <div class="form-row">
          <label>密码</label>
          <input v-model="password" type="password" placeholder="请输入密码" />
        </div>
        <button class="btn primary" @click="handleLogin">登录</button>
        <p v-if="errorMessage" class="error">{{ errorMessage }}</p>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from "vue";
import { useRouter } from "vue-router";
import { request } from "../services/api";
import { setAuth, Role } from "../services/auth";

const router = useRouter();
const role = ref<Role>("teacher");
const account = ref("");
const password = ref("");
const errorMessage = ref("");

const accountLabel = computed(() => {
  if (role.value === "teacher") return "教师工号";
  if (role.value === "student") return "学生学号";
  return "管理员账号";
});

const endpointMap: Record<Role, string> = {
  teacher: "/auth/teacher/login",
  student: "/auth/student/login",
  admin: "/auth/admin/login",
};

const handleLogin = async () => {
  errorMessage.value = "";
  if (!account.value.trim() || !password.value.trim()) {
    errorMessage.value = "账号和密码不能为空";
    return;
  }
  try {
    const data = await request<any>(endpointMap[role.value], {
      method: "POST",
      body: JSON.stringify({
        account: account.value.trim(),
        password: password.value.trim(),
      }),
    });
    setAuth({
      role: data.role,
      id: data.id,
      name: data.name,
      admin_no: data.admin_no,
      teacher_no: data.teacher_no,
      student_no: data.student_no,
    });
    router.push(`/${data.role}`);
  } catch (err: any) {
    errorMessage.value = err.message || "登录失败";
  }
};
</script>
