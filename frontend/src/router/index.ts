import { createRouter, createWebHistory, RouteRecordRaw } from "vue-router";
import LoginView from "../views/LoginView.vue";
import TeacherView from "../views/TeacherView.vue";
import StudentView from "../views/StudentView.vue";
import AdminView from "../views/AdminView.vue";
import { getAuth } from "../services/auth";

const routes: RouteRecordRaw[] = [
  { path: "/", redirect: "/login" },
  { path: "/login", component: LoginView },
  {
    path: "/teacher",
    component: TeacherView,
    meta: { requiresAuth: true, role: "teacher" },
  },
  {
    path: "/student",
    component: StudentView,
    meta: { requiresAuth: true, role: "student" },
  },
  {
    path: "/admin",
    component: AdminView,
    meta: { requiresAuth: true, role: "admin" },
  },
];

const router = createRouter({
  history: createWebHistory(),
  routes,
});

router.beforeEach((to) => {
  const auth = getAuth();
  if (to.path === "/login" && auth) {
    return `/${auth.role}`;
  }
  if (to.meta.requiresAuth) {
    if (!auth) {
      return "/login";
    }
    if (to.meta.role && auth.role !== to.meta.role) {
      return `/${auth.role}`;
    }
  }
  return true;
});

export default router;
