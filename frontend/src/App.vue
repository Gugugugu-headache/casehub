<template>
  <div class="page">
    <header class="hero">
      <div class="brand">
        <div class="logo">CH</div>
        <div>
          <h1>CaseHub 电子信息案例库</h1>
          <p>让案例管理、检索与对话更高效</p>
        </div>
      </div>
      <div class="hero-actions">
        <button class="btn primary">进入系统</button>
        <button class="btn ghost">查看使用指引</button>
      </div>
    </header>

    <section class="roles">
      <article class="card stagger">
        <h2>管理员</h2>
        <ul>
          <li>审核上传</li>
          <li>班级/用户管理</li>
          <li>统一知识库维护</li>
        </ul>
        <button class="btn light">管理员登录</button>
      </article>
      <article class="card stagger">
        <h2>教师</h2>
        <ul>
          <li>嵌入管理</li>
          <li>对话与检索</li>
          <li>案例维护</li>
        </ul>
        <button class="btn light">教师登录</button>
      </article>
      <article class="card stagger">
        <h2>学生</h2>
        <ul>
          <li>案例检索</li>
          <li>学习对话</li>
          <li>上传案例</li>
        </ul>
        <button class="btn light">学生登录</button>
      </article>
    </section>

    <section class="quick-links">
      <div class="chip">数据看板</div>
      <div class="chip">检索统计</div>
      <div class="chip">模型设置</div>
      <div class="chip">系统公告</div>
    </section>
  </div>
</template>

<script setup lang="ts"></script>
