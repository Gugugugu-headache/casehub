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

    <section class="panel">
      <h2>功能建设中</h2>
      <p class="muted">请在后台继续完善审核、用户与班级管理模块。</p>
    </section>
  </div>
</template>

<script setup lang="ts">
import { useRouter } from "vue-router";
import { clearAuth, getAuth } from "../services/auth";

const router = useRouter();
const auth = getAuth();

const logout = () => {
  clearAuth();
  router.push("/login");
};
</script>
