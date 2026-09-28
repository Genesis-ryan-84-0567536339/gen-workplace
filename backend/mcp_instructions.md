BẮT BUỘC cho mọi agent dùng gen-workplace: ghi công việc lên app để Boss theo dõi được trong chatroom và Kanban.
1. Mỗi việc = 1 phiên: trước khi làm, gọi list_conversations. Nếu đã có phiên "VIEC-<n>: <tên việc>" thì dùng lại conv_id đó. Nếu chưa có thì create_conversation(title="VIEC-<n>: <tên việc>", reuse_existing=true).
2. Chia bước thành task Kanban: create_kanban_task(conv_id=<phiên>, viec_ref="VIEC-<n>" (bắt buộc), title, checklist). Gọi claim_task trước khi làm, và update_task_checklist mỗi khi xong một mục.
3. Ghi tiến độ vào chatroom của phiên: log_session_message(conv_id, content, author) ở mỗi mốc (bắt đầu, giao việc, kết quả, bị chặn, xong). Mỗi tin ngắn, kèm link Issue/PR/commit. Tool này chỉ lưu tin, không gọi AI. Đừng dùng gen_chat để ghi log vì gen_chat sẽ gọi agy trả lời.
4. Giao việc cho agy: post_warroom_message với @<vai> (@backend, @frontend, @devops, @qa, @security, @lead), rồi wait_worker_result(dispatch_id) để lấy kết quả. Ghi kết quả vào phiên (mục 3).
5. Đóng việc: complete_task với evidence thật: commit SHA, URL PR có thật, dispatch:<id>, hoặc file trong ~/gw-reports/. Không có bằng chứng thì chưa được đóng.
6. Quy trình đầy đủ nằm trong skill: repo Genesis-ryan-84-0567536339/Brain → skills/work-style/subskills/gen-workplace-dispatch/SKILL.md.
